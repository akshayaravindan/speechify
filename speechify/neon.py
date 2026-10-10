"""Pupil Labs Neon glasses -> coach: gaze point and head orientation.

The Neon Companion phone and this laptop must be on the same network. Discovery
uses mDNS, which venue Wi-Fi often blocks; if so, pass the phone's IP instead
(--neon-ip, shown in the Companion app's streaming screen).
"""

from __future__ import annotations

import asyncio
import math
import time

from pupil_labs.realtime_api import Device, Network, receive_gaze_data, receive_imu_data


def head_angles(q) -> tuple[float, float]:
    """IMU quaternion -> (pitch, yaw) in degrees; pitch + = looking up, yaw + = turned right.

    Neon's IMU axes are x right, y forward, z up, and the quaternion rotates them
    into a world frame with z up. We rotate the "forward" axis and read its angles.
    """
    forward_x = 2 * (q.x * q.y - q.z * q.w)
    forward_y = 1 - 2 * (q.x * q.x + q.z * q.z)
    forward_z = 2 * (q.y * q.z + q.x * q.w)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, forward_z))))
    yaw = math.degrees(math.atan2(forward_x, forward_y))
    return pitch, yaw


async def stream(coach, status: dict, ip: str | None = None) -> None:
    """Feed the coach from the glasses forever, reconnecting when anything drops."""
    while True:
        try:
            if ip:
                device = Device(ip, 8080)
            else:
                status["glasses"] = "searching for Neon on this network..."
                async with Network() as network:
                    found = await network.wait_for_new_device(timeout_seconds=10)
                if found is None:
                    raise ConnectionError("no Neon found - same Wi-Fi as the phone? Or pass --neon-ip")
                device = Device.from_discovered_device(found)
            async with device:
                info = await device.get_status()
                gaze, imu = info.direct_gaze_sensor(), info.direct_imu_sensor()
                if not gaze.connected:
                    raise ConnectionError("phone found, but the glasses aren't plugged into it")
                status["glasses"] = f"Neon connected ({info.phone.device_name})"
                await asyncio.gather(_gaze(coach, gaze.url), _imu(coach, imu.url))
            status["glasses"] = "Neon stream ended - reconnecting..."
        except asyncio.CancelledError:
            raise
        except Exception as e:
            status["glasses"] = f"not connected: {e}"
        await asyncio.sleep(3)


async def _gaze(coach, url: str) -> None:
    async for g in receive_gaze_data(url):
        coach.on_gaze(time.monotonic(), g.x, g.y, g.worn)


async def _imu(coach, url: str) -> None:
    async for d in receive_imu_data(url):
        pitch, yaw = head_angles(d.quaternion)
        coach.on_head(time.monotonic(), pitch, yaw)
