from typing import List
from abc import ABC, abstractmethod

from app.crud.crud_UE import UE
from app.core.config import QoSProfile
from app.schemas.afSessionWithQos import AsSessionWithQoSSubscription


class AfSessionWithQosInterface(ABC):
    # Tells the endpoint whether the driver sends the allocation notification asynchronously
    # noop (False): QoS is applied immediately after change_qos returns, the endpoint notifies
    # huawei (True): the driver notifies once the async Slice Manager operation is (in)successful (polling)
    notifies_asynchronously: bool = False

    @abstractmethod
    async def change_qos(
        self, subscription: AsSessionWithQoSSubscription, ues: List[UE], qos: QoSProfile
    ) -> None:
        pass

    @abstractmethod
    async def revert_qos(
        self, subscription: AsSessionWithQoSSubscription, ues: List[UE]
    ) -> None:
        pass
