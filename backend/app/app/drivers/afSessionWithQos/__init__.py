import os
from typing import Annotated

from fastapi import Depends

from app.core.config import QoSInterfaceBackend, settings
from app.interfaces.afSessionWithQos import AfSessionWithQosInterface

if settings.qos.backend == QoSInterfaceBackend.HUWAEI:
    from .huawei import HuaweiAfSessionWithQos

    _interface = HuaweiAfSessionWithQos(
        settings.qos.huwaei_api_url,
        settings.qos.huwaei_default_ambrup,
        settings.qos.huwaei_default_ambrdl,
        settings.qos.huwaei_default_slice_dnn,
        settings.qos.huwaei_default_slice_sst,
        settings.qos.huwaei_default_start_sd,
        settings.qos.huwaei_operation_poll_interval,
        settings.qos.huwaei_api_user,
        settings.qos.huwaei_api_password,
    )
else:
    from .noop import NoopAfSessionWithQos

    _interface = NoopAfSessionWithQos()


async def get_interface() -> AfSessionWithQosInterface:
    return _interface


AfSessionWithQosDep = Annotated[AfSessionWithQosInterface, Depends(get_interface)]
