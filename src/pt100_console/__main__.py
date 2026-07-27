"""Entry point: ``python -m pt100_console --config config/console.yaml``."""

import argparse
import logging
import signal
import threading
from pathlib import Path

from .acquisition import AcquisitionLoop
from .config import ConsoleConfig, ensure_console_yaml, load_hardware
from .telegraf import TelegrafSender
from .webapp import ConsoleApp, serve

log = logging.getLogger("pt100_console")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="pt100_console",
        description="Web console for Pt100 acquisition (LabJack U6 Pro + Mux80)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/console.yaml"),
        help="console configuration YAML (created if missing)",
    )
    parser.add_argument("--host", default=None, help="bind host override")
    parser.add_argument("--port", type=int, default=None, help="bind port override")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    ensure_console_yaml(args.config)
    config = ConsoleConfig(args.config)
    hardware = load_hardware(config.hardware_config_path)
    log.info(
        "hardware config: %s (%d sensors, U6 serial %s)",
        hardware["path"],
        len(hardware["sensors"]),
        hardware["serial"],
    )

    sender = TelegrafSender()
    loop = AcquisitionLoop(config, hardware, sender)
    app = ConsoleApp(config, hardware, loop)

    host = args.host or config.server_host
    port = args.port if args.port is not None else config.server_port
    server = serve(app, host, port)
    log.info("web console listening on http://%s:%d/", host, port)

    def request_shutdown(signum, frame):  # noqa: ARG001
        log.info("signal %s received, shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    if hasattr(signal, "SIGHUP"):
        def reload_config(signum, frame):  # noqa: ARG001
            log.info("SIGHUP received, reloading configuration")
            config.load()
            config.change_event.set()

        signal.signal(signal.SIGHUP, reload_config)

    loop.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        loop.stop()
        sender.close()
        config.flush()
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
