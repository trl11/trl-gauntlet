# Datasheets

Vendor documentation for the parts on the bench. Downloaded rather than
linked, because a beam-line session is not the place to discover that a
supplier moved a PDF.

| File | Part | Source |
|---|---|---|
| `LI-GMSL3-USB3.2-datasheet.pdf` | LI-GMSL3-USB3.2 adapter, which carries the MAX96792A | [leopardimaging.com](https://leopardimaging.com/wp-content/uploads/2025/07/LI-GMSL3-USB3.2_Datasheet.pdf) |
| `LI-USB30-AR0820-GMSL3-user-guide.pdf` | The AR0820 board's user guide. Kept because the USB and GMSL3 sections match the IMX728 board and the IMX728 guide is not published | [leopardimaging.com](https://leopardimaging.com/wp-content/uploads/2025/06/LI-USB30-AR0820-GMSL3_User_Guide_20250618.pdf) |

## The parts under irradiation

One per suite in the `radiation_tid` campaign, so a beam-line session has the
register maps to hand.

| File | Part | Suite | Source |
|---|---|---|---|
| `ADS7138-datasheet.pdf` | TI ADS7138, 8-channel 12-bit ADC | `tid_ads7138` | [ti.com](https://www.ti.com/lit/ds/symlink/ads7138.pdf) |
| `LAN7430-LAN7431-datasheet.pdf` | Microchip LAN7430, PCIe to Gigabit Ethernet. DS00002631, and covers the LAN7431 too | `tid_lan7430` | [microchip.com](https://ww1.microchip.com/downloads/aemDocuments/documents/UNG/ProductDocuments/DataSheets/LAN7430-LAN7431-Data-Sheet-DS00002631.pdf) |
| `PIC18F25-26K83-datasheet.pdf` | Microchip PIC18F26K83, 8-bit MCU with CAN. DS40001943C, and covers the 25K83 and the LF parts | `tid_pic18f26k83` | [microchip.com](https://ww1.microchip.com/downloads/en/DeviceDoc/PIC18\(L\)F2526K83-Data-Sheet-DS40001943C.pdf) |
| `TMP100-datasheet.pdf` | TI TMP100, I2C temperature sensor | `tid_tmp100` | [ti.com](https://www.ti.com/lit/ds/symlink/tmp100.pdf) |
| `DS_asm330lhb.pdf` | ST ASM330LHB, 6-axis automotive IMU | `tid_asm330lhb` | [st.com](https://www.st.com/resource/en/datasheet/asm330lhb.pdf) |
| `max96792a.pdf` | ADI MAX96792A deserializer. The device-specific user guide, which is where the register map is | `tid_max96792` | [analog.com](https://www.analog.com/media/en/technical-documentation/user-guides/max96792a-device-specific-user-guide.pdf) |
| `ADI-MAX96793-datasheet.pdf` | ADI MAX96793 serializer datasheet | `tid_max96793` | [analog.com](https://www.analog.com/en/products/max96793.html) |

## Fetching these

Analog Devices, ST and Mouser all refuse an automated fetch: the first two
answer with an HTTP/2 `INTERNAL_ERROR` before a byte of PDF arrives, and Mouser
returns a JavaScript bot challenge in place of the file. These are filters
rather than broken links, so anything they publish is downloaded by hand and
dropped in here. TI and Microchip serve theirs to an ordinary request.

Copy the directory to a rig with

    scp docs/datasheets/* trl@<bench>:~/.config/gauntlet/datasheets/

which is where the landing page reads them from.

## Still missing

- The MAX96793 **user guide**. What is here is the datasheet, which gives the
  part's electrical characteristics but not its register map; the deserializer
  has its user guide and the serializer does not. Reading a serializer register
  by number wants
  [the user guide](https://www.analog.com/media/en/technical-documentation/user-guides/max96793-device-specific-user-guide.pdf).

## Reading the SerDes registers

The camera exposes Leopard Imaging's UVC extension unit, so the GMSL chips can
be read over the same USB connection that carries video. No I2C wiring and no
extra hardware: the CP2112 bridge on the bench is a separate device and is not
involved.

The extension unit is **unit 3**, reached through uvcvideo's `UVCIOC_CTRL_QUERY`
ioctl. Its selectors follow
[`LI01/linux_camera_tool`](https://github.com/LI01/linux_camera_tool), whose
`includes/uvc_extension_unit_ctrl.h` names them. Eleven of the fifteen match
this camera exactly, including the two that matter:

| Selector | Name | Length |
|---|---|---|
| `0x07` | `LI_XU_SENSOR_UUID_HWFW_REV` | 49 B |
| `0x10` | `LI_XU_GENERIC_I2C_RW` | 262 B |

`0x10` carries an I2C transaction:

```
byte 0   bit 7 clear to read, set to write; low bits are the register
         address width, 1 for 8-bit and 2 for 16-bit
byte 1   number of data bytes, minus one
byte 2   slave address, high byte
byte 3   slave address, low byte
byte 4   register address, high byte
byte 5   register address, low byte
byte 6+  data
```

A read is `SET_CUR` with the request, then `GET_CUR` to collect the answer.
The MAX9679x use 16-bit register addresses, so byte 0 is `0x02` for a read.

**A read cannot misconfigure anything; a write can.** Writing an unknown value
into a serializer can drop the link, and mid-irradiation that looks exactly
like a radiation effect. Nothing here writes.

## What answers on this bench

The deserializer sits at slave address `0x84` and identifies itself:

| Register | Meaning | Value read |
|---|---|---|
| `0x0000` | its own I2C address | `0x84` |
| `0x000D` | `DEV_ID` | `0xB7` |
| `0x000E` | `DEV_REV` | `0x06` |
| `0x0013` | `CTRL3`, bit 3 is the GMSL link lock | `0xDA`, so locked |
| `0x0022` | decode errors, link A | `0x00` |
| `0x0023` | decode errors, link B | `0x00` |
| `0x0024` | idle errors | `0x00` |

The three error counters reading zero on a healthy link is the point: they are
the primary radiation signal, so a run records where they started and watches
them climb.

Only `0x84` answered an address scan. The serializer is across the GMSL link
and needs the deserializer's I2C pass-through, which has not been investigated.

Selector `0x07` returns the adapter's own identity, which is worth recording on
every run because it ties results to one physical unit:

```
hw_rev 258   fw_rev 2168
uuid   8a06621b-9041-4ff1-afcc-9f9ea482b59f-20260803
```

Its layout, from the same tool: bytes 0-1 hardware revision with the top four
bits a datatype tag, bytes 2-3 firmware revision, bytes 4-48 the UUID string.
