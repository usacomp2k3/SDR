#!/usr/bin/env bash
# One ACARS channel from ka9q pcmrecord stdin (s16le 12.5 kHz mono).
exec /usr/local/bin/acarsdec -i WP-KMCO-ACARS -m 1 \
  --sndfile="-,subtype=2,channels=1,endian=little" \
  --output json:udp:host=127.0.0.1,port=15550
