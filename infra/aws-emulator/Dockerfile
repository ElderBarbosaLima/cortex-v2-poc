# AWS Emulator (github.com/aws/aws-emulator). There is no published image, so
# we build one from the official Linux package, as the docs describe.
#
# Pinned: package version + sha256 per architecture + GPG signature checked
# against AWS's published key fingerprint. To update: change the version and
# both hashes, then rerun `make probe-emulator` (docs/discovery.md 9.2, Q12).
#
# Image tag: cortex-poc/aws-emulator:1.0.545

ARG BASE=public.ecr.aws/amazonlinux/amazonlinux:2023-minimal@sha256:75a2d4226d9b98c914d7eed878a03bef4f365ad548be5d28d464ccba504cec79

FROM ${BASE}

ARG TARGETARCH
ARG EMULATOR_VERSION=1.0.545
ARG EMULATOR_SHA256_ARM64=d1082db735e385a363ef9983a72f419fdb2e113e0a6917357b0fb027ba2388d6
ARG EMULATOR_SHA256_AMD64=c2459541d26a982239be74d38118bc87cc016907be4c055d8d12d72f575cb001
ARG AWS_GPG_FINGERPRINT=98BCF336A3BE53B734E967C52E5BF49BD940F624
ARG PACKAGES_URL=https://downloads.aws.com/aws-emulator/packages/aws-emulator/LTS
ARG GPG_ASC_URL=https://downloads.aws.com/keys/gpg/current.asc

# The emulator writes its CA, credentials and config under $HOME/.aws/emulator.
# The directory is created here, owned by the runtime uid, so the named volume
# mounted on it inherits that ownership. uid 10001 is the same as the
# org-brain runtime user: the emulator writes those files with mode 0600 and
# the clients must be able to read them (read-only mount).
ENV HOME=/home/emulator \
    EMULATOR_TELEMETRY=off

RUN set -eux; \
    case "${TARGETARCH}" in \
      arm64) arch=aarch64; sha="${EMULATOR_SHA256_ARM64}" ;; \
      amd64) arch=x86_64;  sha="${EMULATOR_SHA256_AMD64}" ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    microdnf install -y --setopt=install_weak_deps=0 gnupg2-minimal; \
    pkg="aws-emulator-${EMULATOR_VERSION}-1-${arch}.rpm"; \
    cd /tmp; \
    curl -fsSLO "${PACKAGES_URL}/${pkg}"; \
    curl -fsSLO "${PACKAGES_URL}/${pkg}.sig"; \
    curl -fsSL -o aws.asc "${GPG_ASC_URL}"; \
    echo "${sha}  ${pkg}" | sha256sum -c -; \
    export GNUPGHOME="$(mktemp -d)"; \
    gpg --batch --show-keys --with-colons aws.asc | grep -q "^fpr:::::::::${AWS_GPG_FINGERPRINT}:"; \
    gpg --batch --dearmor -o aws.gpg aws.asc; \
    gpg --batch --no-autostart --no-default-keyring --keyring /tmp/aws.gpg \
        --verify "${pkg}.sig" "${pkg}"; \
    # microdnf cannot install a local file; the package needs only glibc and
    # has no scriptlets. Its embedded header signature (same key) is checked too.
    rpm --import aws.asc; \
    rpm -K "${pkg}" | grep -q "digests signatures OK"; \
    rpm -i "${pkg}"; \
    microdnf clean all; \
    rm -rf /tmp/* "${GNUPGHOME}"; \
    mkdir -p /home/emulator/.aws/emulator; \
    chown -R 10001:10001 /home/emulator; \
    aws-emulator -v

USER 10001
EXPOSE 9297
# -b 0.0.0.0 only inside the container; the host publishes it on 127.0.0.1.
ENTRYPOINT ["aws-emulator", "-b", "0.0.0.0", "--telemetry", "off"]
