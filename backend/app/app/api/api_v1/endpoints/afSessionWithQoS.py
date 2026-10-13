import logging
from typing import Any, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Request,
    Response,
    BackgroundTasks,
)
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import parse_obj_as
from sqlalchemy.orm import Session
from pymongo import ReturnDocument
from bson.objectid import ObjectId

from app import models
from app.api import deps
from app.core.config import QoSProfile, qosSettings
from app.crud import crud_mongo, ue as crud_ue, user
from app.models import UE
from app.db.session import client
import app.schemas.afSessionWithQos as schemas
from app.drivers.afSessionWithQos import AfSessionWithQosDep, AfSessionWithQosInterface
from app.schemas.commonData import BitRate, Link
from app.core.notification_responder import notification_responder

from .utils import (
    add_notifications,
    ReportLogging,
    decode_supported_features,
    encode_supported_features,
)

router = APIRouter()
router.route_class = ReportLogging
db_collection = "QoSMonitoring"

FEATURE_EthAsSessionQoS_5G = 1 << 2
FEATURE_AlternativeQoS_5G = 1 << 4
FEATURE_QoSMonitoring_5G = 1 << 5
FEATURE_AltQosWithIndParams_5G = 1 << 11
FEATURE_GMEC = 1 << 23

server_supported_features = (
    FEATURE_EthAsSessionQoS_5G
    | FEATURE_AlternativeQoS_5G
    | FEATURE_QoSMonitoring_5G
    | FEATURE_AltQosWithIndParams_5G  # requires AlternativeQoS_5G
    | FEATURE_GMEC  # requires QoSMonitoring_5G & AltQosWithIndParams_5G
)


@router.get(
    "/{scsAsId}/subscriptions",
    response_model=List[schemas.AsSessionWithQoSSubscription],
)
def read_active_subscriptions(
    *,
    scsAsId: str = Path(
        ...,
        title="The ID of the Netapp that creates a subscription",
        example="myNetapp",
    ),
    current_user: models.User = Depends(deps.get_current_active_user),
    http_request: Request,
) -> Any:
    """
    Get subscription by id
    """
    db_mongo = client.fastapi
    retrieved_docs = list(
        map(
            lambda doc: doc["subscription"],
            db_mongo[db_collection].find(
                {"owner_id": current_user.id},
                projection={"_id": False, "subscription": True},
            ),
        )
    )

    http_response = JSONResponse(content=retrieved_docs, status_code=200)
    add_notifications(http_request, http_response, False)
    return http_response


# Callback

qos_callback_router = APIRouter()


@qos_callback_router.post(
    "{$request.body.notificationDestination}", response_class=Response
)
def as_session_with_qos_notification(body: schemas.UserPlaneNotificationData):
    pass


@router.post(
    "/{scsAsId}/subscriptions",
    responses={201: {"model": schemas.AsSessionWithQoSSubscription}},
    callbacks=qos_callback_router.routes,
)
async def create_subscription(
    *,
    scsAsId: str = Path(
        ...,
        title="The ID of the Netapp that creates a subscription",
        example="myNetapp",
    ),
    db: Session = Depends(deps.get_db),
    qos_interface: AfSessionWithQosDep,
    item_in: schemas.AsSessionWithQoSSubscription,
    current_user: models.User = Depends(deps.get_current_active_user),
    http_request: Request,
    background_tasks: BackgroundTasks,
) -> Any:
    db_mongo = client.fastapi

    if item_in.self is not None:
        raise HTTPException(
            status_code=400, detail="The self attribute must not be set"
        )

    validate_subscription(item_in)

    # Retrieve UE using the selected identifier
    ue = None
    id_used = ""
    id_value = ""

    if item_in.ueIpv4Addr is not None:
        id_used = "IPv4"
        id_value = str(item_in.ueIpv4Addr)
        ue = crud_ue.get_ipv4(db=db, ipv4=id_value, owner_id=current_user.id)
    elif item_in.ueIpv6Addr is not None:
        id_used = "IPv6"
        id_value = item_in.ueIpv6Addr.exploded
        ue = crud_ue.get_ipv6(db=db, ipv6=id_value, owner_id=current_user.id)
    elif item_in.macAddr is not None:
        id_used = "MAC"
        id_value = item_in.macAddr
        ue = crud_ue.get_mac(db=db, mac=id_value, owner_id=current_user.id)
    elif item_in.gpsi is not None:
        if item_in.gpsi.startswith("msisdn-"):
            id_used = "MSISDN"
            id_value = item_in.gpsi.removeprefix("msisdn-")
            ue = crud_ue.get_msisdn(db=db, msisdn=id_value, owner_id=current_user.id)
        else:
            id_used = "External ID"
            id_value = item_in.gpsi.removeprefix("extid-")
            ue = crud_ue.get_externalId(
                db=db, externalId=id_value, owner_id=current_user.id
            )
    elif item_in.extGroupId is not None:
        raise HTTPException(
            status_code=400, detail="External Group ID is not supported"
        )

    if ue is None:
        raise HTTPException(status_code=404, detail="UE not found")

    # Check if subscription already exists
    doc = db_mongo[db_collection].find_one(
        {"ues": ue.supi, "owner_id": current_user.id}
    )

    if doc is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Subscription for UE with {id_used} ({id_value}) already exists",
        )

    qos_profile = extract_qos_profile(item_in)

    id = ObjectId()
    item_in.self = parse_obj_as(Link, f"{http_request.url}/{id}")

    # Create the document in mongodb
    serialized_subscription = jsonable_encoder(item_in.dict(exclude_unset=True))
    crud_mongo.create(
        db_mongo,
        db_collection,
        {
            "_id": id,
            "owner_id": current_user.id,
            "ues": [ue.supi],
            "subscription": serialized_subscription,
        },
    )

    if qos_profile is not None:
        background_tasks.add_task(
            alter_qos_task,
            driver=qos_interface,
            subscription=item_in,
            ues=[ue],
            qos_profile=qos_profile,
        )

    # Create the reference resource and location header
    response_header = jsonable_encoder({"Location": item_in.self})

    http_response = JSONResponse(
        content=serialized_subscription, status_code=201, headers=response_header
    )
    add_notifications(http_request, http_response, False)
    return http_response


def get_subscription(subscriptionId: str, current_user: models.User) -> Any:
    db_mongo = client.fastapi

    try:
        id = ObjectId(subscriptionId)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid uuid (24-character hex string)",
        )

    filters = {"_id": id}
    if not user.is_superuser(current_user):
        filters = {"owner_id": current_user.id}
    retrieved_doc = db_mongo[db_collection].find_one(
        filters, {"_id": False, "subscription": True}
    )

    # Check if the document exists
    if retrieved_doc is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    return retrieved_doc["subscription"]


@router.get(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    response_model=schemas.AsSessionWithQoSSubscription,
)
def read_subscription(
    *,
    scsAsId: str = Path(
        ...,
        title="The ID of the Netapp that creates a subscription",
        example="myNetapp",
    ),
    subscriptionId: str = Path(..., title="Identifier of the subscription resource"),
    current_user: models.User = Depends(deps.get_current_active_user),
    http_request: Request,
) -> Any:
    """
    Get subscription by id
    """
    subscription = get_subscription(subscriptionId, current_user)

    http_response = JSONResponse(content=subscription, status_code=200)
    add_notifications(http_request, http_response, False)
    return http_response


@router.put(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    response_model=schemas.AsSessionWithQoSSubscription,
)
def update_subscription(
    *,
    scsAsId: str = Path(
        ...,
        title="The ID of the Netapp that creates a subscription",
        example="myNetapp",
    ),
    subscriptionId: str = Path(..., title="Identifier of the subscription resource"),
    item_in: schemas.AsSessionWithQoSSubscription,
    current_user: models.User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
    qos_interface: AfSessionWithQosDep,
    background_tasks: BackgroundTasks,
    http_request: Request,
) -> Any:
    """
    Update subscription by id
    """
    db_mongo = client.fastapi

    subscription_raw = get_subscription(subscriptionId, current_user)
    subscription = schemas.AsSessionWithQoSSubscription.parse_obj(subscription_raw)

    validate_subscription(subscription)

    # Verify that the identifiers haven't changed
    if (
        subscription.ueIpv4Addr != item_in.ueIpv4Addr
        or subscription.ueIpv6Addr != item_in.ueIpv6Addr
        or subscription.macAddr != item_in.macAddr
        or subscription.gpsi != item_in.gpsi
        or subscription.listUeAddrs != item_in.listUeAddrs
    ):
        raise HTTPException(
            status_code=400, detail="Device identifiers cannot be replaced"
        )

    qos_profile = extract_qos_profile(item_in)
    prev_qos_profile = extract_qos_profile(subscription)

    item_in.self = subscription.self

    # Update the document
    json_data = jsonable_encoder(item_in.dict(exclude_unset=True))
    updated_doc = db_mongo[db_collection].find_one_and_update(
        {"_id": ObjectId(subscriptionId)},
        {"$set": {"subscription": json_data}},
        projection={"_id": False, "ues": True, "subscription": True},
        return_document=ReturnDocument.AFTER,
    )

    ues = crud_ue.get_supi_multi(db=db, supis=updated_doc["ues"])

    if qos_profile is None and prev_qos_profile is not None:
        background_tasks.add_task(
            revert_qos_task,
            driver=qos_interface,
            subscription=item_in,
            ues=ues,
        )
    elif qos_profile is not None and qos_profile != prev_qos_profile:
        background_tasks.add_task(
            alter_qos_task,
            driver=qos_interface,
            subscription=item_in,
            ues=ues,
            qos_profile=qos_profile,
        )

    http_response = JSONResponse(content=updated_doc["subscription"], status_code=200)
    add_notifications(http_request, http_response, False)
    return http_response


@router.patch(
    "/{scsAsId}/subscriptions/{subscriptionId}",
    response_model=schemas.AsSessionWithQoSSubscription,
)
def patch_subscription(
    *,
    scsAsId: str = Path(
        ...,
        title="The ID of the Netapp that creates a subscription",
        example="myNetapp",
    ),
    subscriptionId: str = Path(..., title="Identifier of the subscription resource"),
    item_in: schemas.AsSessionWithQoSSubscriptionPatch,
    current_user: models.User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
    qos_interface: AfSessionWithQosDep,
    background_tasks: BackgroundTasks,
    http_request: Request,
) -> Any:
    """
    Patch subscription by id
    """
    db_mongo = client.fastapi

    try:
        id = ObjectId(subscriptionId)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid uuid (24-character hex string)",
        )

    subscription_raw = get_subscription(subscriptionId, current_user)
    subscription = schemas.AsSessionWithQoSSubscription.parse_obj(subscription_raw)

    supported_features = decode_supported_features(subscription.supportedFeatures or "")
    validate_subscription_base(item_in, supported_features)

    qos_profile = extract_qos_profile(item_in)
    prev_qos_profile = extract_qos_profile(subscription)

    json_data = jsonable_encoder(subscription_raw | item_in.dict(exclude_unset=True))
    updated_doc = db_mongo[db_collection].find_one_and_update(
        {"_id": id},
        {"$set": {"subscription": json_data}},
        projection={"_id": False, "ues": True, "subscription": True},
        return_document=ReturnDocument.AFTER,
    )

    ues = crud_ue.get_supi_multi(db=db, supis=updated_doc["ues"])
    new_subscription = schemas.AsSessionWithQoSSubscription.parse_obj(
        updated_doc["subscription"]
    )

    if qos_profile is None and prev_qos_profile is not None:
        background_tasks.add_task(
            revert_qos_task,
            driver=qos_interface,
            subscription=new_subscription,
            ues=ues,
        )
    elif qos_profile is not None and qos_profile != prev_qos_profile:
        background_tasks.add_task(
            alter_qos_task,
            driver=qos_interface,
            subscription=new_subscription,
            ues=ues,
            qos_profile=qos_profile,
        )

    http_response = JSONResponse(content=updated_doc["subscription"], status_code=200)
    add_notifications(http_request, http_response, False)
    return http_response


@router.delete("/{scsAsId}/subscriptions/{subscriptionId}", status_code=204)
def delete_subscription(
    *,
    scsAsId: str = Path(
        ...,
        title="The ID of the Netapp that creates a subscription",
        example="myNetapp",
    ),
    subscriptionId: str = Path(..., title="Identifier of the subscription resource"),
    current_user: models.User = Depends(deps.get_current_active_user),
    db: Session = Depends(deps.get_db),
    qos_interface: AfSessionWithQosDep,
    background_tasks: BackgroundTasks,
    http_request: Request,
) -> None:
    """
    Delete a subscription
    """
    db_mongo = client.fastapi

    try:
        id = ObjectId(subscriptionId)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid uuid (24-character hex string)",
        )

    filters = {"_id": id}
    if not user.is_superuser(current_user):
        filters = {"owner_id": current_user.id}
    res = db_mongo[db_collection].find_one_and_delete(filters)

    # Check if the document was deleted
    if res is None:
        raise HTTPException(status_code=404, detail="Subscription not found")

    subscripiton = schemas.AsSessionWithQoSSubscription.parse_obj(res["subscription"])
    ues = crud_ue.get_supi_multi(db=db, supis=res["ues"])

    if (
        subscripiton.qosReference is not None
        or subscripiton.altQoSReferences is not None
        or subscripiton.altQosReqs is not None
    ):
        background_tasks.add_task(
            revert_qos_task,
            driver=qos_interface,
            subscription=subscripiton,
            ues=ues,
        )

    add_notifications(http_request, None, False)


def validate_subscription(subscription: schemas.AsSessionWithQoSSubscription) -> None:
    """
    Validates the subscription, removes unsupported fields, and updates the
    supported features.
    """
    client_supported_features = decode_supported_features(
        subscription.supportedFeatures or ""
    )
    supported_features = client_supported_features & server_supported_features

    validate_subscription_base(subscription, supported_features)

    # Unset fields that are not supported
    remove_unsupported_fields(subscription, supported_features)

    # Ensure that the AF only supplies one identifier
    validate_ids(subscription)

    subscription.supportedFeatures = encode_supported_features(supported_features)


def validate_subscription_base(
    subscription: schemas.AsSessionWithQoSSubscriptionBase, supported_features: int
) -> None:
    # Unset fields that are not supported
    remove_unsupported_fields_base(subscription, supported_features)

    # Ensure that the AF only supplies the QoS reference once
    validate_qos_references(subscription)


def remove_unsupported_fields(
    subscription: schemas.AsSessionWithQoSSubscription, supported_features: int
):
    if supported_features & FEATURE_EthAsSessionQoS_5G == 0:
        subscription.macAddr = None
        subscription.__fields_set__.remove("macAddr")

    if supported_features & FEATURE_GMEC == 0:
        subscription.gpsi = None
        subscription.__fields_set__.remove("gpsi")

        subscription.extGroupId = None
        subscription.__fields_set__.remove("extGroupId")


def remove_unsupported_fields_base(
    subscription: schemas.AsSessionWithQoSSubscriptionBase, supported_features: int
):
    if supported_features & FEATURE_QoSMonitoring_5G == 0:
        subscription.qosMonInfo = None
        subscription.__fields_set__.remove("qosMonInfo")

    if supported_features & (FEATURE_AlternativeQoS_5G | FEATURE_GMEC) == 0:
        subscription.altQoSReferences = None
        subscription.__fields_set__.remove("altQoSReferences")

    if supported_features & FEATURE_AltQosWithIndParams_5G == 0:
        subscription.altQosReqs = None
        subscription.__fields_set__.remove("altQosReqs")


BITRATE_UNIT_MULTIPIER = {
    "bps": 1,
    "Kbps": 1e3,
    "Mbps": 1e6,
    "Gbps": 1e9,
    "Tbps": 1e12,
}


def bitrate_to_bps(bitrate: BitRate) -> int:
    parts = bitrate.split()
    number = int(parts[0])
    multiplier = BITRATE_UNIT_MULTIPIER[parts[1]]
    return number * multiplier


async def revert_qos_task(
    driver: AfSessionWithQosInterface,
    subscription: schemas.AsSessionWithQoSSubscription,
    ues: List[UE],
) -> None:
    # TODO: Do we need to send a notification when reverting the QoS?
    try:
        await driver.revert_qos(subscription, ues)
    except Exception as e:
        logging.critical("Failed to revert QoS for UEs", e)


async def alter_qos_task(
    driver: AfSessionWithQosInterface,
    subscription: schemas.AsSessionWithQoSSubscription,
    ues: List[UE],
    qos_profile: QoSProfile,
) -> None:
    assert subscription.self is not None

    events = []

    try:
        await driver.change_qos(subscription, ues, qos_profile)
        if driver.notifies_asynchronously:
            # The driver reports the outcome itself once the change is applied (for huawei)
            return
        events.append(
            schemas.UserPlaneEventReport(
                event=schemas.UserPlaneEvent.SUCCESSFUL_RESOURCES_ALLOCATION
            )
        )
    except Exception as e:
        logging.critical("Failed to apply QoS profile to UEs", e)
        events.append(
            schemas.UserPlaneEventReport(
                event=schemas.UserPlaneEvent.FAILED_RESOURCES_ALLOCATION
            )
        )

    await notification_responder.send_notification(
        subscription.notificationDestination,
        schemas.UserPlaneNotificationData(
            transaction=subscription.self,
            eventReports=events,
        ).dict(exclude_unset=True),
    )


def validate_ids(item_request: schemas.AsSessionWithQoSSubscription) -> None:
    hasIPv4 = item_request.ueIpv4Addr is not None
    hasIPv6 = item_request.ueIpv6Addr is not None
    hasMAC = item_request.macAddr is not None
    hasGPSI = item_request.gpsi is not None
    hasExtGroupId = item_request.extGroupId is not None

    if [hasIPv4, hasIPv6, hasMAC, hasGPSI, hasExtGroupId].count(True) != 1:
        raise HTTPException(
            status_code=400,
            detail="Please enter only one of the ueIpv4Addr, ueIpv6Addr, macAddr, gpsi, or extGroupId attributes in the request",
        )


def validate_qos_references(item_in: schemas.AsSessionWithQoSSubscriptionBase) -> None:
    if item_in.qosReference is not None and item_in.altQosReqs is not None:
        raise HTTPException(
            status_code=400,
            detail="qosReference and altQosReqs are mutually exclusive",
        )

    if item_in.altQoSReferences is not None and item_in.altQosReqs is not None:
        raise HTTPException(
            status_code=400,
            detail="altQoSReferences and altQosReqs are mutually exclusive",
        )


def extract_qos_profile(
    item_in: schemas.AsSessionWithQoSSubscriptionBase,
) -> Optional[QoSProfile]:
    qos_profile = None
    qosReferences = item_in.altQoSReferences or []
    if item_in.qosReference is not None:
        qosReferences.insert(0, item_in.qosReference)

    if qosReferences != []:
        for qosReference in qosReferences:
            qos_profile = qosSettings.get_qos_profile(qosReference)
            if qos_profile is not None:
                break
        else:
            raise HTTPException(
                status_code=400, detail="No valid qos reference was supplied"
            )

    if item_in.altQosReqs is not None:
        reqs = item_in.altQosReqs[0]
        qos_profile = QoSProfile(
            uplinkBitRate=(
                bitrate_to_bps(reqs.gbrUl) if reqs.gbrUl is not None else None
            ),
            downlinkBitRate=(
                bitrate_to_bps(reqs.gbrDl) if reqs.gbrDl is not None else None
            ),
            packetDelayBudget=reqs.pdb,
            packerErrRate=reqs.per,
        )

    return qos_profile
