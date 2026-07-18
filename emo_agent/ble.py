"""Bounded BLE discovery and Theater sessions for EMO."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

from .protocol import (
    CHAR_UUID,
    CHUNK_DELAY_SECONDS,
    CHUNK_SIZE,
    ResponseAssembler,
    theater_animation,
    theater_operation,
    theater_speech,
)


class EmoBleError(RuntimeError):
    """Base error whose message is safe to display without device identifiers."""


@dataclass(frozen=True)
class DiscoveredEmo:
    name: str
    address: str
    rssi: int | None
    ble_device: BLEDevice | None = field(default=None, repr=False, compare=False)


async def discover_emos(timeout: float = 8.0) -> list[DiscoveredEmo]:
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    matches: list[DiscoveredEmo] = []
    for address, (device, advertisement) in discovered.items():
        name = device.name or advertisement.local_name or ""
        if name.upper().startswith("EMO"):
            matches.append(
                DiscoveredEmo(
                    name=name,
                    address=address,
                    rssi=getattr(advertisement, "rssi", None),
                    ble_device=device,
                )
            )
    return sorted(matches, key=lambda item: item.name)


async def select_emo(address: str | None, timeout: float) -> DiscoveredEmo:
    if address:
        device = await BleakScanner.find_device_by_address(address, timeout=timeout)
        if device is None:
            raise EmoBleError("EMO n'est plus visible en Bluetooth.")
        return DiscoveredEmo(
            name=device.name or "EMO (adresse fournie)",
            address=address,
            rssi=None,
            ble_device=device,
        )

    devices = await discover_emos(timeout)
    if not devices:
        raise EmoBleError("Aucun EMO détecté. Vérifie le Bluetooth et ferme l'application officielle.")
    if len(devices) > 1:
        names = ", ".join(device.name for device in devices)
        raise EmoBleError(f"Plusieurs EMO détectés ({names}); utilise --address pour en choisir un.")
    return devices[0]


class TheaterSession:
    def __init__(self, device: DiscoveredEmo) -> None:
        self.device = device
        self._client = BleakClient(device.ble_device or device.address)
        self._assembler = ResponseAssembler()
        self._messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._connected = False
        self._entered = False
        self._expired = False

    async def connect(self) -> None:
        try:
            await self._client.connect()
            await self._client.start_notify(CHAR_UUID, self._on_notification)
        except Exception as exc:
            await self.close()
            raise EmoBleError("Connexion BLE impossible; vérifie que l'application officielle est fermée.") from exc
        self._connected = True

    async def enter(self) -> None:
        await self._write(theater_operation("in"))
        response = await self._wait_for("theater_rsp", timeout=10.0)
        if response.get("data", {}).get("result") != 1:
            raise EmoBleError("EMO a refusé ou ignoré l'entrée en mode Theater.")
        self._entered = True

    async def animate(self, animation: str) -> int | None:
        return await self._command_until_complete(theater_animation(animation), timeout=30.0)

    async def speak(self, text: str) -> int | None:
        return await self._command_until_complete(theater_speech(text), timeout=25.0)

    async def close(self) -> None:
        if self._connected and self._entered and not self._expired:
            try:
                await self._write(theater_operation("out"))
                await self._wait_for("theater_rsp", timeout=5.0)
            except Exception:
                pass
        self._entered = False

        if self._connected:
            try:
                await self._client.stop_notify(CHAR_UUID)
            except Exception:
                pass
        try:
            if self._client.is_connected:
                await self._client.disconnect()
        finally:
            self._connected = False

    async def __aenter__(self) -> TheaterSession:
        await self.connect()
        await self.enter()
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    def _on_notification(self, _sender: object, data: bytearray) -> None:
        message = self._assembler.feed(bytes(data))
        if message is None:
            return
        if message.get("type") == "theater_rsp" and message.get("data", {}).get("result") == 10:
            self._expired = True
        self._messages.put_nowait(message)

    async def _write(self, payload: bytes) -> None:
        if self._expired:
            raise EmoBleError("La session Theater a expiré.")
        chunks = [payload[index : index + CHUNK_SIZE] for index in range(0, len(payload), CHUNK_SIZE)]
        for chunk in chunks:
            await self._client.write_gatt_char(CHAR_UUID, chunk, response=False)
            if len(chunks) > 1:
                await asyncio.sleep(CHUNK_DELAY_SECONDS)

    async def _command_until_complete(self, payload: bytes, timeout: float) -> int | None:
        await self._write(payload)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise EmoBleError("Délai dépassé en attendant la fin de l'action Theater.")
            response = await self._wait_for("theater_rsp", timeout=remaining)
            result = response.get("data", {}).get("result")
            if result == 1:
                continue
            if result == 2:
                return result
            raise EmoBleError("EMO est occupé ou a refusé l'action Theater.")

    async def _wait_for(self, message_type: str, timeout: float) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise EmoBleError(f"Délai dépassé en attendant {message_type}.")
            try:
                message = await asyncio.wait_for(self._messages.get(), timeout=min(remaining, 0.5))
            except TimeoutError:
                continue
            result = message.get("data", {}).get("result")
            if result == 10:
                self._expired = True
                raise EmoBleError("La session Theater a expiré.")
            if message.get("type") == message_type:
                return message
