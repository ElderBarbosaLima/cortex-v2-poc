# PR 003 — AWS Emulator in compose + fidelity probe (gate)

Branch: `poc/003-aws-emulator` → `main` · Issue: [docs/tasks.md §3](../tasks.md#3-aws-emulator-no-compose--sonda-de-fidelidade-gate) · Depends on: 001 (merged)

## What
- `infra/aws-emulator/Dockerfile`: no published image exists, so it is built from the official package.
  - Base `public.ecr.aws/amazonlinux/amazonlinux:2023-minimal`, pinned by digest (multi-arch).
  - `aws-emulator-1.0.545-1-<aarch64|x86_64>.rpm`, chosen by `TARGETARCH`, with a **sha256 pinned per architecture**.
  - The **detached GPG signature** is checked against AWS's key, whose fingerprint is pinned (`98BC F336 … D940 F624`). The **embedded RPM signature** is checked too (`rpm -K`).
  - Runs as uid 10001 with `ENTRYPOINT aws-emulator -b 0.0.0.0 --telemetry off`.
- `docker-compose.yml`:
  - `aws-emulator` service: `mem_limit 2g`, volume `aws-emulator-home`, port `127.0.0.1:9297`, healthcheck `curl --cacert … /__admin/health`.
  - `x-aws-client-env` anchor: the SDK variables every AWS client gets.
  - `aws-probe` tool service: `network_mode: service:aws-emulator`, the shared volume mounted read-only.
- `services/org-brain-service/scripts/aws_probe.py` (`make probe-emulator`): plain boto3, configured only by env. It writes `docs/emulator-probe-1.0.545.md` and exits 1 if any required operation fails.
- `boto3` and `cryptography` added to the service (both are needed again in issues 13–14). Ruff and Pyright now also cover `scripts/`.
- `docs/discovery.md` §3.3 records the result; the README has an AWS Emulator section.

## Decisions worth a look
- **Clients reach the emulator on `127.0.0.1` inside its network namespace.** The probe showed the certificate's SANs are `localhost`, `127.0.0.1` and `::1`, and hostname verification fails for `aws-emulator`. With this setup TLS verification stays on and there is no alias trick.
  - Cost: in issue 13, org-brain's host port moves to the `aws-emulator` service.
- **uid 10001 shared by the emulator and org-brain's runtime user.** The emulator writes its CA and credentials with mode `0600`, so a client with a different uid could not read them from the read-only mount.
- **Pinned the latest version (1.0.545) that passes the probe** (Q12). The version, the sha256 of both RPMs and the probe result are in the report.
- **Rotation is proven without writing keys down.** The report shows only a 12-character sha256 prefix of the access key id.
- The amazonlinux minimal `microdnf` cannot install a local RPM, so `rpm -i` is used. This is safe: the package depends only on glibc and has no scriptlets (checked with `rpm -qpR` and `--scripts`).

## Acceptance criteria — output

**1. `make dev` → `aws-emulator` healthy; no key in `.env`, code or image** (clean clone `~/cortex-v2/.clean-check-003`)
```
$ make dev && make ps
cortex-poc-aws-emulator-1       (healthy) 127.0.0.1:9297->9297/tcp
cortex-poc-org-brain-service-1  (healthy) 127.0.0.1:5008->5008/tcp
cortex-poc-postgres-1           (healthy) 127.0.0.1:5432->5432/tcp
$ grep -rIl -E "EMULATOR[0-9A-F]{12}|aws_secret_access_key" --exclude-dir=.git .     → (nothing)
$ docker run --rm --entrypoint sh cortex-poc/aws-emulator:1.0.545 -c 'ls -A ~/.aws/emulator/ | wc -l'   → 0
```

**2. `make probe-emulator` → report; every required operation ok** (full table: [`docs/emulator-probe-1.0.545.md`](../emulator-probe-1.0.545.md))
```
[required    ] ok  s3   CreateBucket / PutBucketVersioning / GetBucketVersioning (Status=Enabled)
[required    ] ok  s3   PutObject (VersionId=yes) / GetObject (body round-trips, UTF-8 Japanese) / HeadObject
[required    ] ok  s3   GetBucketAcl, Policy, Cors, Website, Logging, Tagging, Lifecycle, Replication,
                        Encryption, Accelerate, RequestPayment, OwnershipControls, PublicAccessBlock,
                        ObjectLockConfiguration (Terraform refresh)
[object-lock ] ok  s3   CreateBucket(ObjectLockEnabledForBucket) / PutObjectLockConfiguration /
                        GetObjectLockConfiguration (COMPLIANCE 1 day) / PutObject(COMPLIANCE) / GetObjectRetention
[object-lock ] ok  s3   DeleteObject(VersionId) under COMPLIANCE retention is refused: AccessDenied
[required    ] ok  kms  CreateKey / DescribeKey / CreateAlias / ListAliases / GenerateDataKey(AES_256, EncryptionContext)
                        / Decrypt (unwrap DEK) / Encrypt + Decrypt
[optional    ] ok  kms  Decrypt with a different EncryptionContext is refused: InvalidCiphertextException
[expected-501] ok  kms  TagResource            ← no longer 501 in this version
[required    ] ok  kms  ScheduleKeyDeletion(7 days) → KeyState=PendingDeletion
[required    ] ok  kms  Decrypt after ScheduleKeyDeletion is refused (crypto-shred): KMSInvalidStateException
[optional    ] ok  kms  Decrypt after DisableKey is refused: DisabledException
[required    ] ok  secretsmanager  CreateSecret / DescribeSecret / GetResourcePolicy / PutSecretValue / GetSecretValue (latest)
[optional    ] ok  sts  GetCallerIdentity (Account=000000000000)
Gate: PASS — report written to /docs/emulator-probe-1.0.545.md
```
TLS section of the report:
```
- SAN DNS names: `localhost`
- SAN IP addresses: `127.0.0.1`, `::1`
- Hostname verification: `127.0.0.1` passes, `localhost` passes, `aws-emulator` fails
```

**3. Restarting the emulator rotates credentials and the probe passes again**
```
$ docker compose restart aws-emulator && docker compose run --rm aws-probe
Gate: PASS — report written to /docs/emulator-probe-1.0.545.md
key fingerprint before=f2a835e470e0 after=58a06cc552bf
```

**4. Image builds for `linux/arm64` and `linux/amd64`**
```
$ docker buildx build --builder cortex-poc-multiarch --platform linux/arm64,linux/amd64 infra/aws-emulator; echo exit=$?
exit=0
      1 aws-emulator-1.0.545-1-aarch64.rpm: OK
      1 aws-emulator-1.0.545-1-x86_64.rpm: OK
      2 gpg: Good signature from "SSM Publishing <ssm-publishing@amazon.com>"
      2 Version: 1.0.545
```
(`make build-multiarch` now builds this image as well.)

**Definition of done**
```
$ make test       → 4 passed, 7 deselected
$ make lint       → All checks passed! / 18 files already formatted
$ make typecheck  → 0 errors, 0 warnings, 0 informations
```

## What the probe changes in the plan
- **Object Lock works**, retention included (delete is refused). The Q11 fallback (xfail) should not be needed in issues 5 and 23.
- **Crypto-shredding works as designed** (Q10), and `EncryptionContext` binding works too. That lets issue 14 tie each DEK to its tenant and record.
- **KMS `TagResource` is no longer 501.** Nothing depends on it, because KMS keys stay out of Terraform.
- **"Works in the emulator" is still not "works on S3/COS"** (R3), and IAM is not enforced (the policies in issue 5 stay review artefacts).

## Found while testing
- `gnupg2-minimal` has no `gpg-agent` or `gpgv`. The fingerprint is checked with `gpg --show-keys`, and the signature with `gpg --verify` against a dearmored keyring with `--no-autostart`. A tampered package is rejected (`BAD signature`, exit 1).
- `microdnf` cannot install a local file, so `rpm -i` is used, as explained above.
- A bind mount from the session scratchpad (`/private/tmp`) is empty inside Colima, so all verification happens in the image build.
