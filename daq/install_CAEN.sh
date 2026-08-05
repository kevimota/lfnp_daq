#!/bin/sh
set -e
 
ROOT_DIR=$(pwd)
LIB_DIR="$ROOT_DIR/caen-libs"

PREFIXES="CAENVMELib CAENComm CAENDigitizer"
 
for prefix in $PREFIXES; do
    match=$(find "$LIB_DIR" -mindepth 1 -maxdepth 1 -type d -name "${prefix}*" | sort | head -n 1)
 
    if [ -z "$match" ]; then
        echo "ERROR: no folder matching '${prefix}*' found in $LIB_DIR" >&2
        echo "       Did you forget to download and extract $prefix into daq/caen-libs/?" >&2
        exit 1
    fi
 
    echo "Installing $(basename "$match")..."
    cd "$match/lib"
    sh install_x64
    cd "$ROOT_DIR"
done


#PREFIXES="CAENDPPLib CAENHVWrapper"
# NOTE: CAENUSBdrvB is intentionally NOT installed here. It is a DKMS
# kernel module that must be built and loaded on the HOST OS (not inside a
# container). It is only required for USB VME bridges (A2818/A3818/V1718).
# See README.md for host-side installation steps.
PREFIXES="CAENHVWrapper"

for prefix in $PREFIXES; do
    match=$(find "$LIB_DIR" -mindepth 1 -maxdepth 1 -type d -name "${prefix}*" | sort | head -n 1)
 
    if [ -z "$match" ]; then
        echo "ERROR: no folder matching '${prefix}*' found in $LIB_DIR" >&2
        echo "       Did you forget to download and extract $prefix into daq/caen-libs/?" >&2
        exit 1
    fi
 
    echo "Installing $(basename "$match")..."
    cd "$match"
    ./install.sh
    cd "$ROOT_DIR"
done