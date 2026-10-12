# Menagerie Capture

Ubuntu desktop prototype for the miniature capture station, with motor control, live camera preview, single photos, and automatic six-angle capture. Mini Scan adds Menagerie API uploads and persistent drafts.

## Run

From the Menagerie repository root:

```bash
python3 -m venv capture/.venv
capture/.venv/bin/python -m pip install -e ./capture
capture/.venv/bin/python -m capture_station
```

Requires Python 3.10+ and Tkinter (Ubuntu package `python3-tk`). Select **Simulator** and Connect to try the full control flow without hardware. The simulator uses the same motion state machine as the Pico; it does not validate electrical or mechanical behavior.

## Pico installation

### Upload from VS Code

Upload support uses MicroPython's official [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html), without a VS Code extension. It is installed in this workspace's virtual environment. For a fresh environment, run `capture/.venv/bin/python -m pip install -e './capture[pico]'`.

1. Open the `Menagerie` repository as a VS Code workspace folder.
2. Disconnect the desktop app from the Pico and close Thonny/other serial terminals.
3. Choose **Terminal → Run Task → Pico: Upload firmware**.
4. Wait for **All six firmware files verified**, **MicroPython firmware import checks passed**, and the final upload success message. The Pico restarts automatically; reconnect the desktop app.

The task runs `tools/flash_pico.py`, selects the sole detected MicroPython Pico, disables motor EN and the GP22 light output, copies dependencies before `main.py`, verifies each file with SHA-256 on the Pico, and freshly imports the supporting modules on MicroPython with small protocol/diagnostic checks before requesting a reset. This catches import-time compatibility errors that desktop Python compilation cannot detect, such as unsupported stepped slices. It does not test physical sensor connections. It replaces these six station files, leaving unrelated files alone. This uploads Python files to an existing MicroPython installation; it does not install MicroPython itself. BOOTSEL mode is not needed.

Equivalent terminal commands:

```bash
capture/.venv/bin/python tools/flash_pico.py
# Select explicitly when multiple boards are connected:
capture/.venv/bin/python tools/flash_pico.py --port /dev/ttyACM0
# Inspect without uploading:
capture/.venv/bin/python tools/flash_pico.py --dry-run
```

### Manual upload

Alternatively, use Thonny to copy `firmware/pico/main.py`, `firmware/pico/motion.py`, `firmware/pico/pn532.py`, `firmware/pico/rfid_config.py`, `firmware/pico/diagnostics.py`, and `firmware/pico/lighting.py` to the root of the MicroPython Pico filesystem. Configure the PN532 mapping as described below before expecting tag scans. Restart the Pico, then close/disconnect Thonny so the desktop application can own its USB serial port. Choose a `/dev/serial/by-id/…` port in the app and connect. Uploads occur only when you run the upload task or copy files yourself.

The confirmed PN532 wiring is **MISO GP12, SS GP13, SCK GP14, MOSI GP15**. This follows the revised wiring plan and supersedes previous assignments. The confirmed motor assignments are implemented in `firmware/pico/main.py`:

| TMC2209 signal | Pico GPIO |
|---|---|
| EN (active-low) | GP21 |
| DIR | GP20 |
| STEP | GP19 |

RFID is enabled in `firmware/pico/rfid_config.py` with the confirmed GP12–GP15 mapping. GP15 is dedicated to PN532 MOSI; motor EN uses GP21. Set `PN532_PINS = None` to disable RFID if needed.

The Hall assignment is **GP26 (physical pin 31)** with pull-up, active-low input. Sensor VCC connects to **3V3(OUT), physical pin 36**, not a GPIO. Keep **Hall sensor installed** unchecked until a compatible sensor is wired. The firmware does not configure TMC2209 UART or change its microstep/current settings. For a confirmed 3.3 V-compatible digital, active-low Hall sensor:

| Sensor connection | Pico connection | Physical pin |
|---|---|---:|
| Digital OUT | GP26 | 31 |
| VCC | 3V3(OUT) | 36 |
| GND | GND (shared ground) | 18 |

Confirm the sensor's own pin order and voltage requirements before wiring. Copy the updated firmware files listed above onto the Pico, restart the Pico, and restart the desktop app. `motion.py` does not need to change for this pin reassignment. Keep Hall disabled until the sensor is installed.

The handoff's VREF/current-limit issue remains unresolved. Verify the driver current limit before extended powered testing. Firmware and simulator checks do not establish a suitable current setting.

## Calibrate and operate

1. Start with 200 pulses/second; click **Apply settings**. Speed is a requested pulse rate, not measured RPM. Firmware timing and load may reduce actual speed.
2. Use short clockwise/counterclockwise calibration jogs. If clockwise is reversed when viewed from above, select **Reverse motor direction** and apply.
3. Mark the shaft or turntable and measure how far a known jog turns it. Calculate `pulses per turn = jog pulses × 360 / observed degrees`. Enter an integer and apply. Use the final turntable's revolution count, including belt reduction; repeat calibration after changing the drive or microstep wiring. No microstep setting is assumed.
4. The **60°** buttons now work. For example, a *verified* 3200 pulses per motor revolution with a 3:1 reduction gives 9600 pulses per turntable revolution. Fractional pulse endpoints are rounded without cumulative drift over six moves.
5. Orient the miniature and click **Set current position as home**. This establishes a temporary reference without a sensor. **Return home** travels to the nearest equivalent full-turn home position, so it takes at most half a turn.
6. Increase pulse speed gradually and observe movement. Missed steps cannot be detected by this open-loop controller. There is no acceleration ramp in this bench prototype; re-establish home after stalls or manual movement. Approximate RPM is `60 × pulses/second / pulses per turn`.

The motor holds position after moving. **Release motor** disables it and clears home. **Stop** interrupts motion and clears home but retains holding torque. Disconnecting releases the motor; loss of host heartbeats also releases it after approximately two seconds. A restart/reconnect never restores a persisted home position.

With a Hall sensor installed and the turntable calibrated, **Find Hall home** first leaves an already-active sensor and then seeks the next active edge counterclockwise at up to 200 pulses/second. It stops with an error after two configured turns or at most 120 seconds. A sensor home marks the platform reference; miniature orientation still depends on placement.

## Camera and capture

No Pico firmware update is needed for camera support. The laptop opens the UVC camera directly through OpenCV/V4L2.

1. Open the **Camera and capture** tab. Select **HD USB Camera** and click **Open camera**. The app defaults to 3264 × 2448 and requests MJPEG; the actual negotiated dimensions appear alongside the resolution selector. Close the camera to change its resolution. Preview is resized for display; saved PNGs retain the full acquired resolution.
2. Adjust the physical lens focus and lighting while watching the preview. Exposure and white balance use the camera's existing settings.
3. Select an output directory and settling delay (default one second). **Capture photo** works without a connected motor. Select your miniature identifier on the **Motor and miniature** tab before starting a session.
4. For **Capture six angles**, first connect/calibrate the motor and establish home. The sequence returns to home (or runs Hall homing when configured), waits for vibration to settle, captures 0°, then repeats at 60°, 120°, 180°, 240°, and 300° before returning home. Each move must report completion before settling/capture begins. The camera continuously reads frames and discards three additional frames after each capture request to avoid saving a buffered preview frame.
5. Double-click a saved photo in the list to review it, or click **Open session folder**. To retake a view, position the turntable with the motor controls and use **Capture photo**; this creates a separate session and preserves the originals.

Motor controls are locked while a capture is active, except Stop and Release. **Cancel capture / Stop**, motor disconnects, camera errors, and timeouts end the sequence and retain partial results. Stop clears the motor reference, so establish home again before starting another six-angle sequence. No automatic return motion is attempted after a failure.

Files are saved immediately under `<output>/<miniature identifier>/<session UUID>/`. Each session contains full-resolution PNG originals and an atomically updated `session.json` with the identifier/type, nominal angles, reported motor positions, actual image dimensions, timestamps, camera device, and completion status. Cancelled or failed sessions are marked accordingly. If cancellation overlaps image encoding, a final PNG may exist without a manifest entry; only manifest-listed photos belong to the recorded session. Storage errors are reported without deleting earlier images.

Choose **Simulator** independently for the camera and motor to test the whole workflow without hardware. The simulated camera produces visibly labelled frames, and the manifest records simulated devices. The connected HD USB camera was verified to deliver and save 3264 × 2448 images; full six-angle sequencing was tested with simulated motion.

## Lighting control

Both Capture Station (**Camera and capture** tab) and Mini Scan have a **Lighting** panel with a **Light on** toggle and a **0–100% Brightness** slider. Connect to the Pico to use it; motor calibration is not needed for lighting. Use **Simulator** to try the controls without the LED strips connected. The simulator reports simulated output, not physical light measurements.

The Pico drives **GP22 (physical pin 29)** at **20 kHz PWM**, controlling the ANMBEST MOSFET module and the 12 V TOPAI COB strip. Brightness changes the duty cycle; 0% produces no output and 100% is continuously on. Moving the slider while the toggle is off remembers a level without turning the light on. Brightness is saved in each app's settings, but the on/off state is never restored: startup and new host sessions start off. State shown in the app comes from acknowledged firmware commands and heartbeat telemetry. Older firmware leaves the controls disabled and shows an update message.

Light settings are locked during motion and capture, including settling and camera-only captures, to keep exposure consistent across a sequence. Set the light and allow camera exposure/white balance to settle before capture. Disconnecting/closing sends a light-off command; if communication is lost, a firmware watchdog switches it off after approximately two seconds, independently of whether the motor is enabled. A failed capture leaves the light at its current setting for review; it does not automatically re-enable a light switched off by a disconnect. Physical PWM dimming and camera banding still need testing once the strips are connected.

Upload all **six** firmware files with **Pico: Upload firmware** and restart the desktop app. The upload tool verifies `lighting.py` along with the existing files and runs portable lighting checks on MicroPython; it does not energize the LEDs during those checks. See [wiring guide](../docs/wiring.md#lighting-and-detachable-frame) for the detachable frame connection. The LED-side wiring is currently incomplete while waiting for the male barrel connector; software/simulator operation does not require that connection.

Serial example (brightness is an integer from 0 to 100; `enabled` is a JSON boolean):

```json
{"id":42,"cmd":"LIGHT","enabled":true,"brightness":50}
{"id":43,"cmd":"LIGHT","enabled":false}
```

`PING`/`STATUS` and command replies include a separate `lighting` object with `enabled`, `brightness`, `gpio`, `frequency`, and `duty_u16`. Simulated telemetry also sets `simulated: true`. The off command retains the selected brightness; turning on without a brightness argument uses that level.

## RFID setup and scanning

1. Set the reader's switches/jumpers to **SPI mode** according to that module's markings, then power-cycle it. Do not guess switch positions from another PN532 board.
2. `firmware/pico/rfid_config.py` already enables the confirmed signal mapping:

   ```python
   PN532_PINS = {'miso': 12, 'cs': 13, 'sck': 14, 'mosi': 15}
   ```

   Each signal must use a different pin from GP12–GP15. The driver validates the mapping before setting GPIO modes and rejects assignments to the motor or Hall pins. Software SPI allows the four signals to be rearranged within that group.
3. Upload all six firmware files listed above to the Pico root, preferably with the VS Code upload task. `motion.py` is unchanged, but the upload task includes it so the installation is complete.
4. Restart the Pico, close Thonny, restart the desktop app, and connect to the physical Pico. The RFID status below the miniature identifier shows whether the reader is unconfigured, starting, ready, or reporting an error.
5. Hold one miniature's tag near the PN532 antenna. Its UID automatically becomes the selected miniature identifier. Remove it, place the miniature on the turntable, and use **Capture six angles** after establishing motor home. The Camera tab displays the selected identifier as well.

Automatic polling reads ISO14443A tag UIDs of 4, 7, or 10 bytes, plus ATQA and SAK, and emits a serial event such as:

```json
{"type":"rfid","uid":"04AABBCC112233","uid_length":7,"atqa":"0044","sak":"00","technology":"ISO14443A"}
```

The app saves these fields with the selected identifier and includes them in new capture manifests. This reads tag identity, not user memory, NDEF contents, or encrypted blocks; no writes or authentication attempts are made. Other RF protocols are not polled in this implementation.

A stationary tag is reported once. Removing it for two empty polling cycles permits it to be scanned again. **Scan RFID again** forces the next detected tag to be reported even if it has not been removed, and retries reader initialization after errors. The motor Simulator does not emulate physical tag reads; manual UID entry remains available.

SPI work pauses while the motor moves, and tag events during an active capture are ignored by the app to preserve its miniature selection. Scan again after capture if you presented another tag during a session. Tag removal does not clear the selected miniature, so it can be carried from the scan pad to the turntable.

The driver has bounded ACK/response timeouts, verifies frame checksums and response commands, and uses finite passive-activation retries. Reader errors are separate `rfid_error` events and do not disable motor control or abort captures. Initialization retries every five seconds while idle. `PING`/`STATUS` responses include reader status so reconnecting the app does not depend on seeing a boot-time event. If the reader is unconfigured, set the mapping; if it times out, check the signal wiring, SPI mode and power. Firmware and app tests use simulated PN532 responses; physical tag reads have not yet been verified with this new driver.

Protocol reference: [NXP PN532 User Manual](https://www.nxp.com/docs/en/user-guide/141520.pdf), including SPI framing, SAMConfiguration, RFConfiguration, and InListPassiveTarget.

## Sensor connection tests

Upload the latest firmware and restart the desktop app. Connect on the Motor tab, then open **Sensor tests**. No motor calibration, motion, or Hall homing is needed to test the sensors. Test buttons are disabled during motion/capture.

**Hall:** click **Start / reset Hall test**, then move the magnet toward and away from the sensor. The panel displays GP26's raw HIGH/LOW value, active-low interpretation, and transition count. Firmware samples every 10 ms and sends the current state and counts with regular heartbeat replies, so short transitions can be counted between UI updates. The test reports a signal change only after observing both levels. A disconnected pulled-up input can read HIGH indefinitely; a stuck LOW can indicate a nearby magnet, a short, or incompatible sensor output. A change alone does not establish correct magnet placement—check that it follows your magnet. The Hall-installed checkbox controls homing, not this read-only diagnostic.

**RFID:** click **Test RFID reader / scan tag**. This clears prior success and performs fresh PN532 initialization and a firmware-version query. **SPI communication verified** confirms a valid response over the configured SPI wiring. Then present a tag: the panel shows UID, UID length, ATQA and SAK. A reader communication success and a successful tag read are separate checks. Timeouts cannot distinguish wrong interface mode from incorrect wiring or missing power.

SPI mode is selected on the **PN532 module**, not a Pico switch. Check the module's own SPI switch/jumper legend and power-cycle after changing it. Board variants route their mode switches differently, so exact switch positions require the module model or its documentation. A successful SPI diagnostic is the practical confirmation that the module is communicating in SPI mode.

Motor Simulator telemetry is explicitly marked simulated and cannot verify physical sensor wiring. Sensor commands are `HALL_TEST` (reset observation counters) and `RFID_TEST` (fresh initialization); they never request motor movement. Heartbeat responses now include `sensors.hall` alongside reader status.

## Local identity and API integration

Capture Station saves local sessions for calibration and diagnostics. **New UUID** selects an untagged miniature; the PN532 or manual UID entry selects a tagged miniature. Its settings and sessions live in `$XDG_DATA_HOME/menagerie_capture`, defaulting to `~/.local/share/menagerie_capture`.

For Menagerie API integration, run **Mini Scan** as described below. It uses Menagerie's versioned API for account identity, collection schemas, RFID/UUID lookup, item creation, and appending or replacing existing item photos. Local Capture Station manifests are not API upload payloads.

## Protocol and checks

USB serial uses newline-delimited JSON at 115200 baud. Each request includes an integer `id` and a `cmd`; responses echo the ID with `accepted`, `done`, `status`, or `error`, plus a state snapshot. ID 0 is reserved for heartbeat/status events. Commands: `CONFIG`, `PING`, `STATUS`, `JOG`, `ROTATE`, `ZERO`, `RETURN`, `HOME`, `STOP`, `RELEASE`, `RFID_SCAN`, `RFID_STATUS`, `RFID_TEST`, `HALL_TEST`, `LIGHT`. Asynchronous RFID events use `rfid`, `rfid_status`, or `rfid_error` types and do not complete motor commands.

Example requests:

```json
{"id":1,"cmd":"CONFIG","steps_per_rev":9600,"speed":200,"invert":false,"hall":false}
{"id":2,"cmd":"ROTATE","degrees":60}
{"id":3,"cmd":"STOP"}
```

`JOG` takes signed `steps`; positive means clockwise with the configured direction mapping. Send `PING` every 0.5 seconds while connected. All motion advances cooperatively so input remains serviced during long moves and homing. Position is based on emitted pulses, not an encoder.

```bash
capture/.venv/bin/python -m unittest discover -s capture/tests -v
```

## Mini Scan companion

The original `capture_station` application remains available for calibration and diagnostics.
Mini Scan is a separate dark grey and purple application:

```sh
capture/.venv/bin/python -m pip install -e ./capture
capture/.venv/bin/python -m mini_scan
```

VS Code also has **Mini Scan: Run application**. Close/disconnect Capture Station before connecting Mini Scan to the Pico.

1. Run the updated Menagerie server. In its **Settings → Manage API keys**, generate a key (up to five active keys per account).
2. Enter the server URL and key in Mini Scan. Remote servers require HTTPS; localhost HTTP is supported for development. Remembered keys use the operating system keyring, never the draft/settings files. If the keyring is unavailable, the app reports that the key was not saved and uses it for the current session only.
3. Connect and select a collection. The dialog uses its existing field definitions plus Menagerie's built-in UUID, Name, Description, and Tags. Mini Scan automatically populates Menagerie’s built-in **UUID** for every item; a separate Mini ID field is not required. Dropdown fields use the collection’s configured options. Server-declared missing/wrong-type requirements produce an edit list and open the collection editor; Mini Scan does not change the collection silently.
4. Select the Pico, enter the calibrated pulses per turn and speed, and connect. Calibration defaults are imported from Capture Station when available. Use **Apply motor settings** to update calibration while connected. For magnets every 60°, enter the pulses between magnets and click **Use interval × 6**: 210 interval pulses means 1260 pulses per revolution. Calibration jog buttons work before pulses per revolution is set. Hall homing uses the next magnet as zero; identical magnets cannot provide a unique physical zero across sessions. Select and open the camera.
5. Scan an RFID tag or click **New item without RFID**. Both paths generate a UUID that is shown in the dialog and retained with the draft. A tag has at most one open dialog, with persistent drafts scoped to the server and user. Repeated scans cannot create parallel drafts. Existing uploaded tags open a photo session for that item. Choose **append** to keep existing photos or **replace** to replace all server photos (with confirmation). Item metadata is edited in Menagerie. **Find existing UUID** opens the same workflow for untagged items. **Rescan tag** clears scan suppression so a closed draft can be reopened; Local drafts also reopens it. Manual items do not require an RFID reader or tag. You can attach a tag later using the item’s edit page in Menagerie.
6. Place the miniature and press **Capture 6 photos**. The sequence homes, settles, captures at 0/60/120/180/240/300 degrees, rotates 60 degrees after each image, and confirms the final return to zero before finishing. Only one dialog can capture at a time. STOP or a device failure retains partial photos and stops motion; zero cannot be guaranteed after a fault, so the next sequence homes again.
7. For a retake, choose an angle and **Capture one view**: the station homes, moves to that angle, captures, and returns home. Choose **current** to capture without motor movement or a connected Pico. New views are added to the list; exclude or delete the old view as needed. Original imported/captured files are retained locally. **Restore original** undoes crop/resize changes for newly imported photos; older drafts may have no original.
8. Drag image files onto the dialog's photo list or use **Add images**. Crop with a rectangle, resize in pixels, include/exclude, move up/down for display order, or delete. Only selected finalized JPEGs are uploaded, in list order.
9. Press **Upload to Menagerie**. A successful upload retains its local copy. A connection failure freezes that submission for an identical retry using the same request ID, preventing duplicate items after a lost response. Validation failures return the draft to editable state. Uploaded item details are edited in Menagerie; **Capture more photos** starts another photo session. A definitive identity/submission conflict offers **Start as new untagged item**, preserving photos and fields while generating a fresh UUID and removing the tag. When an owned existing item is identified, **Add photos to existing item** preserves the photos and switches to an append session. Uncertain failures remain frozen for identical retries.

Before upload, Mini Scan checks name length, required fields and formats, photo count, pixel limits, and actual multipart size against limits advertised by Menagerie. A reverse proxy can impose a smaller limit; configure it consistently with Menagerie.

After one successful connection, **Work offline** lets you choose a cached account and collection definitions for that server. Capture new tagged or untagged drafts and reopen saved drafts without a server connection. No API key is stored in the account cache. Close dialogs and reconnect with the same account before uploading. Tagged offline drafts are looked up at upload time; existing tags can switch to adding photos instead of creating duplicate items. Cached schemas may be stale; server validation remains authoritative.

Drafts and images live in `~/.local/share/mini_scan` (or `$XDG_DATA_HOME/mini_scan`).
**Local drafts / storage** shows total usage, opens drafts, deletes selected local copies with confirmation, and configures a cache target (default 5 GiB). Cleanup removes oldest successfully uploaded, closed drafts only; unsent or uncertain drafts are never automatically removed. Consequently this is a cleanup target, not a hard disk quota. Uploaded records in Menagerie are unaffected by local cleanup.

The device's firmware and GPIO assignments do not change for Mini Scan. Lighting control is available in both desktop apps. Rotation uses calibrated STEP pulse counts; magnets provide Hall home references rather than closed-loop indexing. Test camera focus, calibration jogs and Hall homing before a physical capture run.

To load new dropdown options added on Menagerie’s collection page, close the item dialog and refresh collections, then reopen the saved draft. Its UUID and entered values are retained.

### Manual focus and photo adjustments

Click a detail in either camera preview or drag a region to measure its sharpness
from native camera pixels. Turn the lens manually and compare the current score
with the best score for that region. Keep the subject, exposure and lighting fixed:
the number is relative, not a universal quality threshold. A region without enough
texture reports no score. Reset best when changing the scene. Changing the region,
camera or acknowledged lighting settings resets the comparison automatically.

Mini Scan's **Edit photo** adds exposure (±2 stops), contrast, saturation and
sharpening to crop/resize, with a before-adjustments comparison. Original files
remain available through Restore original. Sharpening cannot recover missed focus.

Every new camera photo records acquisition time, native dimensions, camera,
simulation flags, focus method/region/score and acknowledged light settings.
Disconnected or unacknowledged lighting is unknown, not assumed off. Settings
remain attached to originals when editing and are sent in image order to Menagerie.
Menagerie automatically creates standardized Advanced capture summary fields;
these stay hidden until **Show advanced fields** is enabled in its menu. Older
photos and imported files remain valid without capture records. Update/restart
Menagerie before uploading records. Physical dimming still needs verification
when the detachable LED frame wiring is completed.


## Repository layout and development

Desktop sources live in `capture/src/`, desktop tests in `capture/tests/`, Pico
sources in `firmware/pico/`, the uploader in `tools/flash_pico.py`, and KiCad files
in `hardware/capture-station/`. See [setup](../docs/capture-setup.md) and
[API compatibility](../docs/capture-api.md). Keep the repository intact when
installing `capture/`: setuptools includes the portable Pico modules as
`menagerie_pico`, so simulation and physical hardware use the same state machines.
The web app has its own dependencies in the root `requirements.txt`.
