#!/bin/sh
# Only the optional Docker target uses this launcher. Its isolation boundary is
# the dedicated non-root container; no privileged mode or extra capabilities.
exec /usr/bin/chromium --no-sandbox "$@"
