import asyncio
import itertools
import logging
import time
import uuid
from typing import List, Optional

import httpx

from app.crud.crud_UE import UE
from app.core.config import QoSProfile
from app.core.notification_responder import notification_responder
from app.schemas.afSessionWithQos import (
    AsSessionWithQoSSubscription,
    UserPlaneEvent,
    UserPlaneEventReport,
    UserPlaneNotificationData,
)
from app.interfaces.afSessionWithQos import AfSessionWithQosInterface
from app.schemas.commonData import Dnn, Snssai

# Consecutive failed polls of an async operation tolerated before giving up
MAX_POLL_RETRIES = 3

class HuaweiAfSessionWithQos(AfSessionWithQosInterface):
    notifies_asynchronously = True

    def __init__(
        self,
        slice_manager_api_url: str,
        default_ambrup: int,
        default_ambrdl: int,
        default_slice_dnn: str,
        default_slice_sst: int,
        default_start_sd: int,
        operation_poll_interval: float,
        api_user: str,
        api_password: str,
    ) -> None:
        super().__init__()

        self.httpx_client = httpx.AsyncClient(
            base_url=slice_manager_api_url,
            # The API is slow to respond, so disable timeouts
            timeout=None
        )
        self.default_ambrup = default_ambrup
        self.default_ambrdl = default_ambrdl
        self.default_slice_dnn = default_slice_dnn
        self.default_slice_sst = default_slice_sst
        # SDs handed out to new slices when the subscription doesn't supply one
        self._sd_counter = itertools.count(default_start_sd)
        self.operation_poll_interval = operation_poll_interval
        self._poll_tasks: set[asyncio.Task] = set()

    async def _get_slice_association(
        self, dnn: Dnn, snssai: Optional[Snssai], ue: UE
    ) -> dict | None:
        params = {"dnn": dnn}
        if snssai is not None and snssai.sd is not None:
            params["snssai"] = f"{snssai.sst}-{snssai.sd}"

        res = await self.httpx_client.get(
            f"/core/ues/{ue.supi}/slice-associations", params=params
        )

        if res.status_code == 404:
            return None
        if not res.is_success:
            raise Exception(
                f"Error while getting slice associations in Slice Manager for {ue.supi} (status code: {res.status_code}): {res.text}"
            )

        associations = res.json()["associations"]
        return associations[0] if associations else None

    async def _change_ue_qos(
        self,
        subscription: AsSessionWithQoSSubscription,
        ue: UE,
        ambrup: int | None,
        ambrdl: int | None,
        notify: bool,
        ambr_from_association: bool = False,
    ) -> None:
        dnn = subscription.dnn or ue.dnn or self.default_slice_dnn
        snssai = subscription.snssai
        association = await self._get_slice_association(dnn, snssai, ue)

        if association is not None:
            # Slice exists -> patch the UE's own QoS for that slice association (AMBR), leaving the slice untouched
            slice_id = association["slice_id"]
            if ambr_from_association:
                # Prefer the AMBR configured on the association, using the given values as fallback
                ambrup = association.get("uplink_aggregate_maximum_bitrate_kbps", ambrup)
                ambrdl = association.get("downlink_aggregate_maximum_bitrate_kbps", ambrdl)
            logging.debug("Patching QoS of UE %s (slice %s)", ue.supi, slice_id)
            payload: dict = {
                "uplink_aggregate_maximum_bitrate_kbps": ambrup,
                "downlink_aggregate_maximum_bitrate_kbps": ambrdl,
                "slice_id": slice_id,
            }
            benchmark_resource = "Patch UE QoS"
            benchmark_op = "PATCH"
            start_ns = time.perf_counter_ns()
            res = await self.httpx_client.patch(
                f"/core/ues/{ue.supi}/qos",
                json={k: v for k, v in payload.items() if v is not None},
            )
            elapsed_ns = time.perf_counter_ns() - start_ns
        else:
            # TODO Slice doesn't exist -> create new slice and associate UE (2 multiple async operations)
            # TODO raised exception as I will not be using this for now
            # slice_id = str(uuid.uuid4())
            # sst = snssai.sst if snssai is not None else self.default_slice_sst
            # sd = snssai.sd if snssai is not None else f"{next(self._sd_counter):06d}"
            # logging.debug("Creating slice %s for UE %s", slice_id, ue.supi)
            # payload = {
            #     "slice_id": slice_id,
            #     "dnn": dnn,
            #     "sst": sst,
            #     "sd": sd,
            #     "uplink_maximum_bitrate_per_ue_kbps": ambrup,
            #     "downlink_maximum_bitrate_per_ue_kbps": ambrdl,
            # }
            # benchmark_resource = "Create slice QoS"
            # benchmark_op = "POST"
            # start_ns = time.perf_counter_ns()
            # res = await self.httpx_client.post(
            #     "/core/slices",
            #     json={k: v for k, v in payload.items() if v is not None},
            # )
            # elapsed_ns = time.perf_counter_ns() - start_ns
            raise Exception(
                f"UE {ue.supi} has no slice association in Slice Manager for DNN {dnn}"
            )

        logging.info(
            'BENCHMARK: {"api": "SliceManager", "resource": "%s", "filter": "slice manager qos", '
            '"op": "%s", "x_correlator": "", "ue": "%s", "time_ns": %d, "status": %d}',
            benchmark_resource,
            benchmark_op,
            ue.supi,
            elapsed_ns,
            res.status_code,
        )

        if not res.is_success:
            raise Exception(
                f"Error while changing QoS in Slice Manager for {ue.supi} (status code: {res.status_code}): {res.text}"
            )

        request_id = res.json()["request_id"]
        logging.debug(
            "Slice Manager accepted QoS change for %s (slice %s, request %s)",
            ue.supi,
            slice_id,
            request_id,
        )

        task = asyncio.create_task(
            self._poll_operation(subscription, ue, request_id, notify)
        )
        self._poll_tasks.add(task)
        task.add_done_callback(self._poll_tasks.discard)

    async def _poll_operation(
        self,
        subscription: AsSessionWithQoSSubscription,
        ue: UE,
        request_id: str,
        notify: bool,
    ) -> None:
        try:
            retries = 0
            while True:
                res = await self.httpx_client.get(f"/operations/{request_id}")
                if not res.is_success:
                    if retries >= MAX_POLL_RETRIES:
                        raise Exception(
                            f"Error while polling operation {request_id} in Slice Manager (status code: {res.status_code}): {res.text}"
                        )
                    retries += 1
                    logging.warning("Polling operation %s failed with status %d (retry %d/%d)", request_id, res.status_code, retries, MAX_POLL_RETRIES)
                    await asyncio.sleep(self.operation_poll_interval)
                    continue
                retries = 0

                operation = res.json()
                if operation["state"] in ("completed", "failed"):
                    break
                await asyncio.sleep(self.operation_poll_interval)

            if operation["state"] == "completed":
                logging.debug(
                    "Slice Manager operation %s for %s completed in %s ms",
                    request_id,
                    ue.supi,
                    operation.get("duration_ms"),
                )
                event = UserPlaneEvent.SUCCESSFUL_RESOURCES_ALLOCATION
            else:
                logging.error(
                    "Slice Manager operation %s for %s failed: %s",
                    request_id,
                    ue.supi,
                    operation.get("components"),
                )
                event = UserPlaneEvent.FAILED_RESOURCES_ALLOCATION
        except Exception as e:
            logging.critical("Failed to poll Slice Manager operation %s: %s", request_id, e)
            event = UserPlaneEvent.FAILED_RESOURCES_ALLOCATION

        if not notify:
            return

        assert subscription.self is not None
        await notification_responder.send_notification(
            subscription.notificationDestination,
            UserPlaneNotificationData(
                transaction=subscription.self,
                eventReports=[UserPlaneEventReport(event=event)],
            ).dict(exclude_unset=True),
        )

    async def change_qos(
        self, subscription: AsSessionWithQoSSubscription, ues: List[UE], qos: QoSProfile
    ) -> None:
        # The Slice Manager API expects the value to be in Kbps but the
        # interface works with bps so we need to divide by 1000 rounding up
        ambrup = qos.uplinkBitRate
        if ambrup is not None:
            ambrup = (ambrup + 999) // 1000

        ambrdl = qos.downlinkBitRate
        if ambrdl is not None:
            ambrdl = (ambrdl + 999) // 1000

        for ue in ues:
            await self._change_ue_qos(subscription, ue, ambrup, ambrdl, notify=True)

    async def revert_qos(
        self, subscription: AsSessionWithQoSSubscription, ues: List[UE]
    ) -> None:
        for ue in ues:
            # Revert to the AMBR of the UE's slice association, with the NEF defaults as fallback.
            # The endpoint doesn't notify on revert, so only log the outcome
            await self._change_ue_qos(
                subscription,
                ue,
                self.default_ambrup,
                self.default_ambrdl,
                notify=False,
                ambr_from_association=True,
            )
