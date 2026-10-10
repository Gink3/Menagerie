# Wiring Layout

## Pico

```text
Physical pin   Pico pin      Connection
------------   ----------    -----------------------------------------------
 1             GP0           Unassigned
 2             GP1           Unassigned
 3             GND           Unassigned
 4             GP2           Unassigned
 5             GP3           Unassigned
 6             GP4           Unassigned
 7             GP5           Unassigned
 8             GND           Unassigned
 9             GP6           Unassigned
10             GP7           Unassigned
11             GP8           Unassigned
12             GP9           Unassigned
13             GND           Unassigned
14             GP10          Unassigned
15             GP11          Unassigned
16             GP12          PN532 MISO
17             GP13          PN532 SS / CS
18             GND           See Shared power and ground
19             GP14          PN532 SCK
20             GP15          PN532 MOSI
21             GP16          Unassigned
22             GP17          Unassigned
23             GND           Unassigned
24             GP18          Unassigned
25             GP19          TMC2209 STEP
26             GP20          TMC2209 DIR
27             GP21          TMC2209 EN
28             GND           Unassigned
29             GP22          See Lighting and detachable frame
30             RUN           Unassigned
31             GP26          Hall sensor OUT
32             GP27          Unassigned
33             AGND          Unassigned
34             GP28          Unassigned
35             ADC_VREF      Unassigned
36             3V3(OUT)      See Shared power and ground
37             3V3_EN        Unassigned
38             GND           Unassigned
39             VSYS          Unassigned
40             VBUS          Unassigned
```

## Shared power and ground

```text
Pico 3V3(OUT), pin 36
    +--> PN532 VCC
    +--> TMC2209 VDD
    +--> Hall sensor VCC

Pico GND, pin 18
    +--- PN532 GND
    +--- TMC2209 GND
    +--- Hall sensor GND
    +--- 12 V PSU negative

12 V PSU positive --> fuse --> TMC2209 VM
```

## Motor and capacitor

```text
NEMA 17 coil A --> TMC2209 A1 / A2
NEMA 17 coil B --> TMC2209 B1 / B2

100 µF / 25 V capacitor + --> TMC2209 VM
100 µF / 25 V capacitor - --> TMC2209 GND
```

## Lighting and detachable frame

Components: TOPAI 12 V COB strip (B0BXL1X1ZH) and ANMBEST MOSFET switch (B07NWD8W26). GP22 control is implemented with 20 kHz PWM for brightness plus an on/off toggle. Both desktop apps start off; host heartbeat loss switches the output off after approximately two seconds.

```text
Pico GP22, physical pin 29 --> ANMBEST PWM/TRIG
Pico GND                  --> ANMBEST signal GND
12 V PSU positive --> fuse --> ANMBEST DC+/VIN+
12 V PSU negative         --> ANMBEST DC-/VIN- (shared ground)

ANMBEST OUT+ --> detachable barrel connector positive --> LED strip +12 V
ANMBEST OUT- --> detachable barrel connector negative --> LED strip negative
```

Use the labels on the actual module to identify its input and load terminals. The LED frame's two wires both return to the module's **load output**: do not connect LED negative directly to shared supply ground, which would bypass low-side switching. The Pico supplies only the control signal, not LED power.

The LED-side connection is not yet complete; the male barrel connector is pending so the light frame can be detached. Match its dimensions to the strip's existing socket, and verify the socket/plug polarity before terminating it (center positive only if that matches the strip). Use a connector, wire, fuse and 12 V supply rated for the installed strip length's current; allow for the motor's load if sharing the PSU. Disconnect 12 V power before attaching or removing the frame connector.

Until assembly is complete, leave the unused load wires insulated and the light toggle off. Simulator controls, firmware command checks and motion tests do not verify physical dimming, wiring polarity, module heating or camera banding. Once connected, first check on/off and then several brightness levels while inspecting the camera preview and saved photos.
