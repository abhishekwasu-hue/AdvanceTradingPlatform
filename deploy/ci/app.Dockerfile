# CI staging stack only (deploy/ci/compose.ci.yml): the release's own backend image plus libfaketime, so stack.sh can
# move the API's and the worker's clock into an NSE session (LD_PRELOAD + FAKETIME, empty = real clock). Nothing else
# changes; production never builds or runs this image.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends libfaketime \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(dpkg -L libfaketime | grep -m1 '/libfaketimeMT.so.1$')" /usr/local/lib/libfaketimeMT.so.1 \
    && test -e /usr/local/lib/libfaketimeMT.so.1
USER appuser
