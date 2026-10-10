# RF Device with Learning

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant integration that turns an **RF ceiling-fan remote** into proper
Home Assistant entities — by **learning** the remote's codes with a Broadlink
RM Pro / RM4 Pro and replaying them through Home Assistant's RF transmitter or
its managed Broadlink connection for native RM4 Pro packets.

No code lists, no `SmartIR` JSON, no template fans. You point the wizard at your
Broadlink, press the buttons on your remote when prompted, and you get a real
**fan entity with speeds**, an optional **light**, and any **extra buttons** you
want — all from the UI.

## Why

Home Assistant's Broadlink integration can learn IR and act as a `remote`, but it
doesn't give you a *fan* with speed control. This integration does:

- A guided **"learn your remote" wizard** — define how many speeds your fan has,
  whether it has a separate On button, a light, and any custom buttons, then learn
  each one by pressing it on the remote.
- Creates an **optimistic `fan` entity** (Off + N speeds, optional On), an optional
  **`light` entity**, and one **`button` entity** per custom code.
- **Re-learn or add buttons** any time from the entry's *Configure* screen.

## Requirements

- **Home Assistant 2026.5 or newer** (the `radio_frequency` platform ships in 2026.5).
- A **Broadlink RM Pro / RM4 Pro** (or other RF-capable Broadlink) already set up in
  the **Broadlink integration**. Select its `radio_frequency` transmitter;
  native RM4 Pro packets are sent through that device's Broadlink connection.
- An RF fan remote on a supported band (Broadlink covers **433 MHz** and **315 MHz**).

## Installation

### HACS (recommended)

1. In HACS, open the three-dot menu → **Custom repositories**.
2. Add `https://github.com/clevrdavid/rf_fan` with category **Integration**.
3. Install **RF Device with Learning**, then **restart Home Assistant**.

*(Once accepted into the HACS default store, you'll be able to search for it
directly without adding the custom repository.)*

### Manual

Copy `custom_components/rf_fan/` into your Home Assistant `config/custom_components/`
directory and restart.

## Setup

1. **Settings → Devices & Services → Add Integration → RF Device with Learning.**
2. Give the fan a name and tell the wizard about its remote:
   - **Speed count** — how many discrete speeds (e.g. 3).
   - **Separate On button** — tick if the remote has a dedicated On (some only have
     speed buttons + Off).
   - **Has a light** — tick if the remote has a light toggle.
   - **Custom buttons** — a comma/newline-separated list of any extras (e.g.
     `Reverse, Timer`).
3. The wizard walks you through learning each button. **Press the button on the
   remote when prompted** — and learn each speed by the speed it *actually*
   produces, not the label printed on the remote (some remotes are mislabelled).
4. Finish, and your fan/light/button entities appear.

### Fans that won't learn with the sweep

Some remotes send very short bursts that Broadlink's frequency *sweep* can't lock
onto (e.g. the **Mercator FRM97**). Tick **"Capture without the sweep"** (direct
capture) and set the frequency (usually `433.92` MHz) — it listens at that
frequency directly, like learning an IR code. Directly-captured fans are
automatically transmitted as a cleaned, repeated frame so they trigger reliably:
the most common frame in the capture is re-sent several times, separated by the
inter-frame gap **measured from your own capture** (receivers need their remote's
gap to re-sync — ~1.8 ms on the Mercator FRM97, ~6.5 ms on the Create Windcalm).

### RM4 Pro replay compatibility (1.1.2)

RM4 Pro RF captures use a native `0xB1` packet with a carrier-frequency field
and a flags byte (`0xC0`), not the legacy `0xB2` packet/repeat-count layout.
Rebuilding them as legacy packets can flash the RM4 LED without operating the
fan. Native replay preserves the learned carrier and flags and repeats cleaned
frames in the payload through Home Assistant's existing Broadlink connection.

Existing learned codes work without re-learning. Other RF transmitters still
use the `radio_frequency` platform. Native sends update the fan/light's
optimistic state, but not the core RF transmitter's last-send timestamp.

Physical replay is confirmed on Home Assistant 2026.10.0 with a Broadlink
RM4 Pro and a CREATE Windcalm ceiling fan.

### Re-learning

Open the integration entry → **Configure** to re-learn a single button or add a new
one without starting over.

## How it works

The `radio_frequency` platform is **send-only** — it has no learning. So this
integration:

1. **Captures** a code with the Broadlink's own RF learn.
2. **Decodes** the Broadlink pulse packet into raw OOK microsecond timings (the
   exact inverse of Broadlink's encoder).
3. **Transmits** the timings through `radio_frequency.async_send_command`, except
   for native RM4 Pro captures: those retain their carrier/flags header and use
   the selected device's managed Broadlink connection instead of the legacy
   packet encoder.

## Notes & limitations

- **RF is one-way**, so entities are **optimistic** (`assumed_state`) — Home
  Assistant tracks what it last sent; it can't read the fan's actual state. The
  discrete-speed + dedicated-Off design keeps this accurate in practice.
- Learning currently **requires a Broadlink** (it's the capture device).
- Some AC fans run weak on low/medium speeds — that's the fan's speed **capacitor**
  (hardware), not the codes; this integration reproduces the remote faithfully.

## License

[MIT](LICENSE)
