# Getting the Prep's API working

The Prep Interface talks to the instrument's REST API, over a direct Ethernet cable between a laptop and the Prep. Three things have to be true before it connects.
The exact menus on the Prep differ between software versions, so for the Prep-side steps follow Hamilton's own documentation or ask Hamilton support.

## 1. Update the Prep software to the latest version

The REST API is part of the Prep's software and has changed between versions (this project was written against `/NimbusLite/api/v1`). Update the Prep to
the latest release Hamilton provides for it, using Hamilton's update instructions, and do it first: updates can change the API and reset settings, so do
the steps below afterwards.

## 2. Give the laptop a static IP address on the Prep's network

The Prep's internal PC has a fixed address, `192.168.100.101`. Put the laptop on the same network with a fixed address of its own. Connect the laptop's Ethernet port to the Prep with a cable, then set:

| Setting | Value |
| --- | --- |
| IP address | `192.168.100.10` |
| Subnet mask | `255.255.255.0` |
| Gateway / DNS | leave empty (this link only talks to the Prep) |

- **Windows:** Settings → Network & Internet → Ethernet → the adapter → IP assignment → Edit → Manual → IPv4 on, then fill in the table.
- **macOS:** System Settings → Network → Ethernet (or your USB adapter) → Details → TCP/IP → Configure IPv4: Manually.

Check it: `ping 192.168.100.101` should answer. If your lab already uses `192.168.100.x` for something else, use another free address in that range for the laptop.

## 3. Turn off user logins on the Prep

The Prep Interface does not log in, so the Prep's **user management / user logins must be disabled**. Once the software is updated, switch them off in the Prep's own settings
(an administrator account is normally needed). With logins on, the API refuses requests that carry no token. If you would rather keep logins, `prep_api.py` can log in
(`--user`, `--password`), but the web interface cannot.

Think about who can reach the Prep before you do this: with logins off, anything that can reach `192.168.100.101` can control it. Keep it on the direct laptop cable only, and do not bridge or share that network connection.

## 4. Check it

```bash
python3 server.py        # then open http://localhost:8080
```

The **machine pill (top right)** should say "Prep connected". If it says "Prep not connected", press it to retry and check, in order: the cable, the laptop's address (step 2),
that the Prep has finished starting up, and that user logins are off (step 3). You can also test from a terminal:

```bash
curl http://192.168.100.101/NimbusLite/api/v1/system-ready
```

No instrument to hand? Choose **Simulation** from the machine pill to try the whole interface against a built-in fake Prep (see [DEV.md](../DEV.md)).

Always try a new protocol as a **practice run** first.
