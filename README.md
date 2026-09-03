# RF Device with Learning

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant integration that turns an **RF ceiling-fan remote** into proper
Home Assistant entities — by **learning** the remote's codes with a Broadlink
RM Pro / RM4 Pro and replaying them through Home Assistant's native
`radio_frequency` platform.

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
  the **Broadlink integration**. Its `radio_frequency` transmitter is what this
  integration sends through.
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
2. Name the device, pick its **primary entity type** (Fan, Cover, Light or
   Standalone button), and choose the **RF transmitter** and **frequency**.
3. Set the options for that type — **speed count** and **separate On button** for a
   fan, **Stop button** for a cover, **light control type** (one toggle button, or
   separate on and off) for a light. Every type also takes **Custom buttons**, a
   comma/newline-separated list of extra one-press codes (e.g. `Reverse, Timer`),
   and **Capture without the sweep**.
4. The wizard walks you through learning each button. **Press the button on the
   remote when prompted** — and learn each speed by the speed it *actually*
   produces, not the label printed on the remote (some remotes are mislabelled).
5. Finish, and your entities appear. Add a **light**, another fan or more buttons
   later from **Settings → the device → Configure → "Add a new entity"**.

### Fans that won't learn with the sweep

Some remotes send very short bursts that Broadlink's frequency *sweep* can't lock
onto (e.g. the **Mercator FRM97**). Tick **"Capture without the sweep"** (direct
capture) and set the frequency (usually `433.92` MHz) — it listens at that
frequency directly, like learning an IR code. Directly-captured fans are
automatically transmitted as a cleaned, repeated frame so they trigger reliably.

### Re-learning

Open the integration entry → **Configure** to re-learn a single button or add a new
one without starting over.

## How it works

The `radio_frequency` platform is **send-only** — it has no learning. So this
integration:

1. **Captures** a code with the Broadlink's own RF learn.
2. **Decodes** the Broadlink pulse packet into raw OOK microsecond timings (the
   exact inverse of Broadlink's encoder).
3. **Transmits** it back through `radio_frequency.async_send_command` via a small
   `RadioFrequencyCommand` subclass carrying those timings — transmitter-agnostic,
   on Home Assistant's supported rails.

## Notes & limitations

- **RF is one-way**, so entities are **optimistic** (`assumed_state`) — Home
  Assistant tracks what it last sent; it can't read the fan's actual state. The
  discrete-speed + dedicated-Off design keeps this accurate in practice.
- Learning currently **requires a Broadlink** (it's the capture device). Transmit is
  transmitter-agnostic via the `radio_frequency` platform.
- Some AC fans run weak on low/medium speeds — that's the fan's speed **capacitor**
  (hardware), not the codes; this integration reproduces the remote faithfully.

## License

[MIT](LICENSE)
