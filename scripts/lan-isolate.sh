#!/bin/bash
# NOTE: this host runs ROOTLESS Docker (pasta/slirp4netns userspace networking).
# Rootless Docker does NOT create the host DOCKER-USER iptables chain, so the
# classic "iptables -I DOCKER-USER ..." approach does not apply here and will
# fail with "No chain/target/match by that name".
#
# Under rootless Docker:
#  - the daemon and containers run as your unprivileged user, so a container
#    escape lands UNPRIVILEGED (cannot become host root or edit host firewall);
#  - container egress is proxied through a userspace stack (pasta) as your user,
#    which DOES still let a container reach the home LAN.
#
# Effective controls for the remaining LAN-egress gap, in order of preference:
#  1) Move the deployment to the Oracle VM: "the LAN" becomes Oracle's, and the
#     home-network question disappears entirely. (Capacity hunt in progress.)
#  2) Isolate this machine at the ROUTER: device/client isolation or a guest
#     VLAN so this host cannot initiate connections to other LAN devices. This
#     is the correct, robust tool and does not risk the container's internet.
#  3) Accept the layered container posture (below) for the short demo window:
#     rootless + non-root uid 10001 + cap_drop ALL + no-new-privileges, with no
#     web-reachable RCE/SSRF path for an attacker to chain into LAN access.
#
# Do NOT try to force a host nftables rule here: under pasta the container's
# traffic appears to originate from your own user, so a naive rule would also
# cut off your own machine's LAN access.
echo "Rootless Docker detected: host iptables isolation does not apply."
echo "See the comments in this script for the controls that do."
