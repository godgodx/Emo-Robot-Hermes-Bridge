"""Command-line entry point for bounded EMO BLE tests."""

from __future__ import annotations

import argparse
import asyncio

from .ble import EmoBleError, TheaterSession, discover_emos, select_emo
from .protocol import ANIMATIONS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Contrôle BLE local et non interactif d'EMO")
    parser.add_argument("--timeout", type=float, default=8.0, help="durée de découverte BLE en secondes")
    parser.add_argument("--address", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="détecter EMO sans envoyer de commande")

    animate = subparsers.add_parser("animate", help="jouer une animation autorisée")
    animate.add_argument("animation", choices=sorted(ANIMATIONS))

    speak = subparsers.add_parser("speak", help="prononcer une phrase via Theater")
    speak.add_argument("text")
    return parser


async def run(args: argparse.Namespace) -> int:
    if args.command == "scan":
        devices = await discover_emos(args.timeout)
        if not devices:
            print("Aucun EMO détecté.")
            return 2
        for device in devices:
            signal = "inconnu" if device.rssi is None else f"{device.rssi} dBm"
            print(f"Détecté: {device.name} (signal {signal})")
        return 0

    device = await select_emo(args.address, args.timeout)
    print(f"Connexion à {device.name}…")
    async with TheaterSession(device) as session:
        if args.command == "animate":
            result = await session.animate(args.animation)
            print(f"Animation '{args.animation}' envoyée (résultat {result}).")
        else:
            result = await session.speak(args.text)
            print(f"Phrase envoyée (résultat {result}).")
    print("Session BLE fermée proprement.")
    return 0


def main() -> None:
    args = build_parser().parse_args()
    try:
        code = asyncio.run(run(args))
    except (EmoBleError, ValueError) as exc:
        print(f"Erreur: {exc}")
        code = 1
    except KeyboardInterrupt:
        print("Interrompu.")
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
