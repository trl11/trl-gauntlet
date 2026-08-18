# Datasheets

Vendor documentation for the parts on the bench. Downloaded rather than
linked, because a beam-line session is not the place to discover that a
supplier moved a PDF.

| File | Part | Source |
|---|---|---|
| `LI-GMSL3-USB3.2-datasheet.pdf` | LI-GMSL3-USB3.2 adapter, which carries the MAX96792A | [leopardimaging.com](https://leopardimaging.com/wp-content/uploads/2025/07/LI-GMSL3-USB3.2_Datasheet.pdf) |
| `LI-USB30-AR0820-GMSL3-user-guide.pdf` | The AR0820 board's user guide. Kept because the USB and GMSL3 sections match the IMX728 board and the IMX728 guide is not published | [leopardimaging.com](https://leopardimaging.com/wp-content/uploads/2025/06/LI-USB30-AR0820-GMSL3_User_Guide_20250618.pdf) |

## Missing, and why

The two register maps that matter most are published by Analog Devices but
could not be fetched from this container: every attempt fails with an HTTP/2
`INTERNAL_ERROR`, which is a bot filter rather than a broken link. They open
normally in a browser.

- [MAX96792A deserializer user guide](https://www.analog.com/media/en/technical-documentation/user-guides/max96792a-device-specific-user-guide.pdf)
- [MAX96793 serializer user guide](https://www.analog.com/media/en/technical-documentation/user-guides/max96793-device-specific-user-guide.pdf)

Download both by hand and drop them here.

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
