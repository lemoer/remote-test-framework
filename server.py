#!/usr/bin/env python3
import os
import shutil
import subprocess
from result import Err, Ok, Result, is_err
from typing import List

def ser2net_cmd(tty_path: str, speed: int, port: int) -> Result[List[str], str]:
    ser2net_bin = shutil.which("ser2net")
    if ser2net_bin is None:
        if os.path.isfile("/usr/sbin/ser2net"):
            ser2net_bin = "/usr/sbin/ser2net"

        if ser2net_bin is None:
            return Err("ser2net binary not found")

    return Ok([
        ser2net_bin,
        "-d",
        "-n",
        "-Y",
        f"connection: &con01#  accepter: telnet(rfc2217,mode=server),tcp,{port}",
        "-Y",
        f"  connector: serialdev(nouucplock=true),{tty_path},{speed}n81,local",  # pylint: disable=line-too-long
        "-Y",
        "  options:",
        "-Y",
        "    max-connections: 10",
    ])

def ser2net_start(tty_path: str, speed: int, port: int) -> Result[subprocess.Popen, str]:
    cmd = ser2net_cmd(tty_path, speed, port)
    if is_err(cmd):
        raise cmd

    child = subprocess.Popen(cmd.unwrap())

    try:
        child.wait(timeout=0.5)
        return Err(f"ser2net for {cmd[0]} exited immediately")
    except subprocess.TimeoutExpired:
        # good, ser2net didn't exit immediately
        pass

    return Ok(child)

def ser2net_stop(child: subprocess.Popen) -> Result[None, str]:
    child.terminate()
    child.wait()
    return Ok(None)

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <tty_path> <speed> <tcpport>")
        sys.exit(1)

    tty_path = sys.argv[1]
    speed = int(sys.argv[2])
    port = int(sys.argv[3])

    child = ser2net_start(tty_path, speed, port)
    if is_err(child):
        print(child)
        sys.exit(1)

    child = child.unwrap()

    print(f"ser2net started with PID {child.pid}")
    try:
        child.wait()
    except KeyboardInterrupt:
        ser2net_stop(child)
    print("ser2net exited")
