# Wake-on-LAN Setup

Wake-on-LAN depends on the PC, network adapter, switch, and router allowing the
magic packet to reach a powered-off machine.

Before using Secret Grounds Wake PC:

1. Enable Wake-on-LAN or PCIe wake in BIOS/UEFI.
2. In Windows Device Manager, open the network adapter power settings and allow
   the device to wake the computer.
3. Enable the adapter's Wake on Magic Packet option.
4. Prefer wired Ethernet. Wi-Fi Wake-on-LAN support is inconsistent.
5. Keep the PC connected to power and the same LAN broadcast domain as the
   server.
6. Save the PC MAC address in Secret Grounds Manager if automatic detection is
   unavailable or incorrect.

The server records Wake-on-LAN as `packet_sent`. It does not claim the PC booted
successfully; the dashboard waits for the client heartbeat/WebSocket to return.
