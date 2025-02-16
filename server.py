#!/usr/bin/env python3
import os
import shutil
import asyncio
import subprocess
from result import Err, Ok, Result, is_err
from typing import List, Dict
from pydantic import BaseModel

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

from pyroute2 import IPRoute

def iface_set_static_ip(interface: str, ip_address: str, mask: int = 24) -> Result[None, str]:
    with IPRoute() as ip:
        try:
            idx = ip.link_lookup(ifname=interface)[0]
            ip.flush_addr(index=idx)

            # Verify flush
            addresses = ip.get_addr(index=idx)
            if len(addresses) > 0:
                return Err(f"Failed to flush IP addresses from iface {interface}")

            ip.addr('add', index=idx, address=ip_address, mask=mask)

            # Verify set
            addresses = ip.get_addr(index=idx)
            assigned_ips = [entry.get('attrs', [])[0][1] for entry in addresses]
            if ip_address in assigned_ips:
                return Ok(None)
            else:
                return Err(f"Tried to add IP {ip_address} to iface {interface}, but it wasn't there afterwards.")
        except IndexError:
            return Err(f"Iface {interface} not found.")
        except PermissionError:
            return Err(f"Root rights are necessary to set ip on iface {interface}.")

def gpio_prepare_output(gpio: int, active_low: bool, gpio_name: str) -> Result[None, str]:
    export_file = "/sys/class/gpio/export"
    gpio_path = f"/sys/class/gpio/gpio{gpio}"

    if not os.path.exists(export_file):
        return Err("GPIO export file not found")

    if not os.path.isdir(gpio_path):
        with open(export_file, "w") as f:
            f.write(f"{gpio_path}\n")

        if not os.path.isdir(gpio_path):
            return Err(f"{gpio_name} gpio {gpio} could not be exported.")

    with open(f"{gpio_path}/direction", "w") as f:
        f.write("out\n")

    with open(f"{gpio_path}/direction") as f:
        if f.read().strip() != "out":
            return Err(f"{gpio_name} gpio {gpio} could not be set to output mode.")

    active_low_file_content = "1" if active_low else "0"
    with open(f"{gpio_path}/active_low", "w") as f:
        f.write(active_low_file_content)

    with open(f"{gpio_path}/value") as f:
        if f.read().strip() != active_low_file_content:
            return Err(f"active_low state for {gpio_name} gpio {gpio} could not be set.")

    return Ok(None)

def gpio_set_value(gpio: int, value: int) -> Result[None, str]:
    gpio_path = f"/sys/class/gpio/gpio{gpio}"
    if not os.path.isdir(gpio_path):
        return Err(f"GPIO {gpio} not exported")

    with open(f"{gpio_path}/value", "w") as f:
        f.write(f"{value}\n")

    return Ok(None)

def gpio_get_value(gpio: int) -> Result[int, str]:
    gpio_path = f"/sys/class/gpio/gpio{gpio}"
    if not os.path.isdir(gpio_path):
        return Err(f"GPIO {gpio} not exported")

    with open(f"{gpio_path}/value") as f:
        return Ok(int(f.read().strip()))

from fastapi import FastAPI, HTTPException

class Device(BaseModel):
    name: str

    def prepare(self) -> Result[None, str]:
        return Err("not implemented")

    async def is_powered_on(self) -> Result[bool, str]:
        return Err("not implemented")

    async def power_off(self) -> Result[None, str]:
        return Err("not implemented")

    async def power_on(self) -> Result[None, str]:
        return Err("not implemented")

    async def power_cycle(self) -> Result[None, str]:
        return Err("not implemented")

    async def push_reset_button(self, seconds: int) -> Result[None, str]:
        return Err("not implemented")

    async def flash_by_url(self, url: str) -> Result[None, str]:
        return Err("not implemented")

class Device1(Device):
    power_gpio: int
    power_gpio_inverted: bool = False
    reset_gpio: int
    reset_gpio_inverted: bool = False

    def prepare(self) -> Result[None, str]:
        power_gpio_prepare_result = gpio_prepare_output(self.power_gpio, self.power_gpio_inverted, "Power")
        if is_err(power_gpio_prepare_result):
            return power_gpio_prepare_result

        return gpio_prepare_output(self.reset_gpio, self.reset_gpio_inverted, "Reset")

    async def is_powered_on(self) -> Result[bool, str]:
        return gpio_get_value(self.power_gpio).map(lambda x: x == 1)

    async def power_off(self) -> Result[None, str]:
        return gpio_set_value(self.power_gpio, 0)

    async def power_on(self) -> Result[None, str]:
        return gpio_set_value(self.power_gpio, 1)

    async def power_cycle(self) -> Result[None, str]:
        power_off_result = await self.power_off()
        if is_err(power_off_result):
            return power_off_result

        await asyncio.sleep(1)

        return await self.power_on()

    async def push_reset_button(self, seconds: int) -> Result[None, str]:
        turn_gpio_on_result = gpio_set_value(self.reset_gpio, 1)
        if is_err(turn_gpio_on_result):
            return turn_gpio_on_result

        await asyncio.sleep(seconds)

        return gpio_set_value(self.reset_gpio, 0)

    async def flash_by_url(self, url: str) -> Result[None, str]:
        return Err("not implemented")

app = FastAPI()

class DevicesListResult(BaseModel):
    device_names: List[str]

devices: Dict[str, Device] = {}

devices["device1"] = Device1(name="device1", power_gpio=539, reset_gpio=529, reset_gpio_inverted=True, power_gpio_inverted=True)

device_init_failed = False
for device_name, device in devices.items():
    if is_err(device.prepare()):
        print(f"Failed to prepare device {device_name}: {device.prepare().unwrap_err()}")
        device_init_failed = True

if device_init_failed:
    exit(1)

iface_set_ip_result = iface_set_static_ip("eth0", "192.168.0.2", 24)
if is_err(iface_set_ip_result):
    print(iface_set_ip_result.unwrap_err())
    exit(1)

@app.get("/")
async def list_devices() -> DevicesListResult:
    return DevicesListResult(device_names=list(devices.keys()))

@app.get("/{device_name}/power")
async def device_power(device_name: str) -> str:
    device = devices.get(device_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await device.is_powered_on()
    if is_err(result):
        raise HTTPException(status_code=500, detail=result.unwrap_err())

    return "on" if result.unwrap() else "off"

@app.post("/{device_name}/power/off")
async def device_power_off(device_name: str) -> str:
    device = devices.get(device_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await device.power_off()
    if is_err(result):
        raise HTTPException(status_code=500, detail=result.unwrap_err())

    return "ok"

@app.post("/{device_name}/power/on")
async def device_power_on(device_name: str) -> str:
    device = devices.get(device_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await device.power_on()
    if is_err(result):
        raise HTTPException(status_code=500, detail=result.unwrap_err())

    return "ok"

@app.post("/{device_name}/power/cycle")
async def device_power_cycle(device_name: str) -> str:
    device = devices.get(device_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await device.power_cycle()
    if is_err(result):
        raise HTTPException(status_code=500, detail=result.unwrap_err())

    return "ok"

@app.post("/{device_name}/push_reset_button/{seconds}")
async def device_push_reset_button(device_name: str, seconds: int) -> str:
    device = devices.get(device_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await device.push_reset_button(seconds)
    if is_err(result):
        raise HTTPException(status_code=500, detail=result.unwrap_err())

    return "ok"

@app.post("/{device_name}/flash_by_url/{url}")
async def device_flash(device_name: str, url: str) -> str:
    device = devices.get(device_name)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    result = await device.flash_by_url(url)
    if is_err(result):
        raise HTTPException(status_code=500, detail=result.unwrap_err())

    return "ok"

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
        # start the webserver
        import uvicorn
        uvicorn.run(app, host="127.0.0.1")
    except KeyboardInterrupt:
        ser2net_stop(child)
    print("ser2net exited")
