# Pošta Srbije Parcel Tracker

[![Release](https://img.shields.io/github/v/release/ha-parcel-integrations/ha-posta-srbije.svg)](https://github.com/ha-parcel-integrations/ha-posta-srbije/releases)
[![Downloads](https://img.shields.io/github/downloads/ha-parcel-integrations/ha-posta-srbije/total.svg)](https://github.com/ha-parcel-integrations/ha-posta-srbije/releases)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

> 📦 Part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/), a family of package-tracking integrations for Home Assistant that all publish the same parcel format, statuses and events, so they plug into cross-carrier automations and the optional [Parcel Aggregator](https://github.com/ha-parcel-integrations/ha-parcel-aggregator). Browse the other carriers on the website.
>
> 💬 Questions or feedback? Join the discussion on the [Home Assistant community](https://community.home-assistant.io/t/packages-postnl-dhl-nl-dpd-and-gls-parcel-integration/112433/).

> ⚠️ **Pre-release.** Only a handful of Pošta Srbije scan codes have been seen so far, all on parcels sent from Serbia abroad. Parcels sent within Serbia or to Serbia may carry codes this integration does not know yet. It logs a warning with a ready-to-paste issue link when it meets one.

A custom Home Assistant integration that tracks your [Pošta Srbije](https://www.posta.rs) (Serbian Post) parcels and package shipments. No account is needed — you enter the tracking code yourself. Pošta Srbije publishes status, scan history and weight for a parcel; it gives no delivery window, pickup point, sender or recipient, so this integration shows none.

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Options](#options)
- [Removal](#removal)
- [Sensors](#sensors)
- [Parcel status reference](#parcel-status-reference)
- [Events](#events)
- [Services](#services)
- [Examples](#examples)
- [Debugging](#debugging)
- [Troubleshooting](#troubleshooting)
- [Related integrations](#related-integrations)
- [Disclaimer](#disclaimer)
- [Contributing](#contributing)
- [License](#license)

## Features

- Track any number of Pošta Srbije parcels by tracking code — no account needed
- Per-parcel sensor with the canonical status (`registered` / `in_transit` / `at_pickup_point` / `delivered` / …), the carrier's own status text and the parcel's weight
- Summary sensors: incoming parcels, parcels awaiting pickup, recently delivered parcels
- `posta_srbije.track_parcel` / `posta_srbije.untrack_parcel` services, so a dashboard button can add a parcel
- Events + device triggers for no-code automations (parcel registered, status changed, delivered)
- Opt-in per-parcel scan history
- Manual refresh button and a diagnostic last-update sensor

## Requirements

- Home Assistant 2025.12 or newer
- A Pošta Srbije parcel and its tracking code: two letters, nine digits and
  two letters, usually ending in `RS` (for example `LX000000014RS`) — no
  account needed
- Home Assistant must be able to reach the internet; each tracked parcel is
  looked up on its own

## Installation

### HACS (recommended)

Click the button below to open this repository in HACS, then install **Pošta Srbije**:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-parcel-integrations&repository=ha-posta-srbije&category=integration)

Or add it by hand: in HACS, choose the three-dot menu → **Custom repositories**, add `https://github.com/ha-parcel-integrations/ha-posta-srbije` as an **Integration** and install **Pošta Srbije**.

Restart Home Assistant if HACS asks you to.

### Manual

Copy `custom_components/posta_srbije` into your `config/custom_components/` folder and restart Home Assistant.

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=posta_srbije)

Add the integration via **Settings → Devices & Services → Add Integration → Pošta Srbije**. There is nothing to fill in: the hub is created immediately (Pošta Srbije tracking needs no account).

Then add parcels via the integration's **Configure** dialog, the [`posta_srbije.track_parcel`](#services) service, or a [dashboard button](examples/dashboards/add_parcel_card.yaml). The tracking code is on your shipping confirmation email or the missed-delivery card.

## Options

Open **Configure** on the integration entry:

| Section | Option | Default | Description |
|---|---|---|---|
| Parcels | Add / remove | — | Manage the tracked tracking codes. Changes apply immediately, no restart. |
| Delivered parcels | Filter by / amount | last 7 days | How long delivered parcels stay visible on the delivered sensor. |
| Parcel history | Include status history | off | Adds a `history` attribute per parcel with each status update. |

Polling isn't one of these settings: the integration polls on a dynamic,
status-driven schedule with nothing to configure.

## Dynamic polling

Polling isn't a setting here — the integration adjusts its own cadence to
what your tracked parcels are actually doing:

- **Quiet hours** — no polling between 00:00–06:00 local time, aside from one
  catch-up check at each end of that window (around midnight and around 6
  AM), so an overnight update is never missed.
- **Hot (every 15 minutes)** — while any tracked parcel is out for delivery
  today, starting an hour before its delivery window opens (or immediately if
  no window is known yet).
- **Normal (every 45 minutes)** — for anything else still on its way.
- **Fully paused** — once every tracked parcel has been delivered, or nothing
  is tracked at all, polling stops until you add a parcel back (adding one
  always triggers an immediate check, regardless of the pause).
- A small, fixed per-hub offset is added on top, so not every Pošta Srbije
  hub out there polls at exactly the same second.

Pošta Srbije reports no "out for delivery" status and no delivery window, so
the hot tier never applies: a parcel in transit is checked about every 45
minutes.

## Removal

Standard HA removal applies: **Settings → Devices & Services → Pošta Srbije → ⋮ → Delete**. Nothing is stored on Pošta Srbije's side.

## Sensors

| Entity | Description |
|---|---|
| `sensor.posta_srbije_incoming_parcels` | Number of active tracked parcels, full list under the `parcels` attribute |
| `sensor.posta_srbije_parcel_<code>` | One per tracked parcel; state is the canonical status, attributes carry the full normalised parcel |
| `sensor.posta_srbije_next_delivery` | Stays empty: Pošta Srbije gives no expected delivery time |
| `sensor.posta_srbije_awaiting_pickup` | Parcels the post office holds for you, full list under the `parcels` attribute |
| `sensor.posta_srbije_delivered_parcels` | Recently delivered parcels (see the retention option) |
| `sensor.posta_srbije_last_successful_update` | Diagnostic: when Pošta Srbije was last polled successfully |

The **Deliveries** calendar (`calendar.posta_srbije_deliveries`) exists but stays empty: Pošta Srbije gives no delivery time.

A delivered parcel moves from its per-parcel sensor to the delivered sensor automatically.

## Parcel status reference

The `status` field is the carrier-agnostic enum shared by the whole integration family:

| Status | Meaning |
|---|---|
| `registered` | Received by Pošta Srbije |
| `in_transit` | Dispatched, in transport or arrived at a post office along the way |
| `at_pickup_point` | The addressee has been notified and the post office holds the parcel |
| `delivered` | Delivered |
| `returning` | Delivered back to the sender |
| `problem` | A delivery attempt failed |
| `unknown` | No history yet, or a scan code we have not mapped yet |

There is no `out_for_delivery`: Pošta Srbije has no scan for it.

The carrier's own human-readable text is always available as `raw_status`.

## Events

The integration fires these on the event bus (also available as device triggers on the Pošta Srbije device):

| Event | When |
|---|---|
| `posta_srbije_parcel_registered` | A new parcel appears in the active list |
| `posta_srbije_parcel_status_changed` | A parcel's canonical status changes (`old_status` / `new_status` in the payload), except the final hop to delivered |
| `posta_srbije_parcel_delivered` | A parcel is delivered |

Every payload is the full normalised parcel plus the hub's `device_id`. Events are suppressed on the first refresh after start-up.

## Services

| Service | Fields | Description |
|---|---|---|
| `posta_srbije.track_parcel` | `tracking_code` | Start tracking a parcel |
| `posta_srbije.untrack_parcel` | `tracking_code` | Stop tracking a parcel |

## Examples

Ready-to-paste automations and dashboard snippets live in [`examples/`](examples/), including tracking a new parcel straight from a dashboard.

### Community Lovelace cards

Third-party cards that work with this integration's sensors:

- [jonisnet/hki-parcels-card](https://github.com/jonisnet/hki-parcels-card)
- [klaptafel/ha-package-tracker-card](https://github.com/klaptafel/ha-package-tracker-card)

## Debugging

```yaml
logger:
  logs:
    custom_components.posta_srbije: debug
```

## Troubleshooting

- **A parcel shows `unknown`** — Pošta Srbije has no history for it yet, or the code is wrong. It will pick up automatically once the parcel is scanned.
- **A delivered parcel keeps its last status** — Pošta Srbije drops the history of an old item after a while; the integration then keeps showing what it last saw.
- **Tracking stopped working for every parcel** — the Pošta Srbije tracking service may have changed. Check the log for a Pošta Srbije warning and [open an issue](https://github.com/ha-parcel-integrations/ha-posta-srbije/issues/new) with it.
- **A status logs "Unrecognised Pošta Srbije status code"** — please [open an issue](https://github.com/ha-parcel-integrations/ha-posta-srbije/issues/new?template=unrecognised_status.yml) with the logged line so the mapping can be extended.

## Related integrations

This integration is part of [**ha-parcel-integrations**](https://ha-parcel-integrations.github.io/) — a family of
parcel-carrier integrations that all publish the same canonical parcel format,
statuses and events.

- [**Parcel Aggregator**](https://github.com/ha-parcel-integrations/ha-parcel-aggregator) rolls every installed carrier
  up into one set of sensors.
- Browse [the organisation](https://ha-parcel-integrations.github.io/) for the current list of supported carriers.

## Disclaimer

This is an independent, community-built project. It is not affiliated with, endorsed by, sponsored by, or supported by Pošta Srbije, Home Assistant, or any other third party referenced in this project. Please don't contact Pošta Srbije for support with this integration.

All third-party trademarks, trade names, product names, logos, and other brand assets are the property of their respective owners. References to them are solely to identify the relevant carrier or service and do not imply affiliation, sponsorship, or endorsement. Nothing in this project grants or implies any licence or right to use third-party brand assets.

This integration may rely on public, unofficial, or undocumented carrier interfaces, accessed with your own account or API key where required. These may change or be withdrawn without notice and may be subject to Pošta Srbije's terms. Data is sent only to Pošta Srbije's own services or those of its group; this project operates no servers of its own. You are responsible for ensuring that your use complies with applicable law and those terms. Use is at your own risk; see the [licence](LICENSE) for warranty limitations.

This integration uses the tracking service behind the Pošta Srbije mobile app. It can change or stop working without notice.

## Contributing

Pull requests and issues are welcome. Please open an issue before
submitting a large change.

## License

[MIT](LICENSE)
