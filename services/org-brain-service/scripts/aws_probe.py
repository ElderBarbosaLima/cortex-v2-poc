"""AWS Emulator fidelity probe (docs/tasks.md, issue 3 — the gate for every AWS issue).

Calls each S3, KMS, Secrets Manager and STS operation the PoC (and the Terraform
AWS provider underneath it) will use, with the normal boto3 SDK configured only by
environment variables, and writes `docs/emulator-probe-<version>.md`.

Groups:
  required      must work, or the plan stops (exit code 1)
  object-lock   immutable retention; a gap is allowed but recorded (Q11: xfail + R3)
  expected-501  known NotImplemented (KMS TagResource); recorded, never required
  optional      useful to know (fallbacks, design choices); recorded only

Nothing here is emulator-specific code: point the same variables at real AWS and
the probe runs unchanged. The emulator never denies a request signed with its own
key, so "ok" here says nothing about IAM permissions.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import socket
import ssl
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from cryptography import x509

REQUIRED = "required"
OBJECT_LOCK = "object-lock"
EXPECTED_501 = "expected-501"
OPTIONAL = "optional"
GROUPS = (REQUIRED, OBJECT_LOCK, EXPECTED_501, OPTIONAL)

NOT_IMPLEMENTED_CODES = frozenset({"NotImplemented", "NotImplementedException"})


class ProbeAssertionError(AssertionError):
    """The call answered, but not with what real AWS would return."""


@dataclass(frozen=True)
class Result:
    service: str
    operation: str
    group: str
    outcome: str  # ok | not-implemented | failed
    detail: str


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeAssertionError(message)


class Probe:
    def __init__(self) -> None:
        cfg = Config(retries={"max_attempts": 1}, s3={"addressing_style": "path"})
        self.s3: Any = boto3.client("s3", config=cfg)
        self.kms: Any = boto3.client("kms", config=cfg)
        self.secrets: Any = boto3.client("secretsmanager", config=cfg)
        self.sts: Any = boto3.client("sts", config=cfg)
        self.results: list[Result] = []
        self.suffix = uuid.uuid4().hex[:10]

    def check(
        self,
        service: str,
        operation: str,
        group: str,
        call: Callable[[], str | None],
        *,
        ok_codes: tuple[str, ...] = (),
        expect_codes: tuple[str, ...] = (),
    ) -> bool:
        """Run one call. `ok_codes`: AWS error codes that mean "implemented, nothing
        configured" (e.g. NoSuchBucketPolicy). `expect_codes`: the call must FAIL
        with one of these (e.g. Decrypt after ScheduleKeyDeletion)."""
        try:
            detail = call() or ""
            if expect_codes:
                outcome, detail = "failed", f"succeeded; expected {'/'.join(expect_codes)}"
            else:
                outcome = "ok"
        except ClientError as exc:
            err = exc.response.get("Error", {})
            code = str(err.get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            message = str(err.get("Message", ""))[:160]
            if status == 501 or code in NOT_IMPLEMENTED_CODES:
                outcome, detail = "not-implemented", f"HTTP {status} {code}: {message}"
            elif code in expect_codes:
                outcome, detail = "ok", f"refused as expected: {code}"
            elif code in ok_codes:
                outcome, detail = "ok", f"answered {code} (implemented, nothing configured)"
            else:
                outcome, detail = "failed", f"HTTP {status} {code}: {message}"
        except ProbeAssertionError as exc:
            outcome, detail = "failed", str(exc)
        except (BotoCoreError, OSError) as exc:
            outcome, detail = "failed", f"{type(exc).__name__}: {exc}"[:200]
        self.results.append(Result(service, operation, group, outcome, detail))
        return outcome == "ok"

    # ── S3 ──────────────────────────────────────────────────────────────────

    def probe_s3(self) -> None:
        bucket = f"cortex-probe-{self.suffix}"
        key = "probe/hello.json"
        body = '{"hello": "架空電機株式会社"}'.encode()

        if not self.check(
            "s3", "CreateBucket", REQUIRED, lambda: self.s3.create_bucket(Bucket=bucket) and None
        ):
            return

        def versioning() -> str:
            self.s3.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
            return ""

        def get_versioning() -> str:
            status = self.s3.get_bucket_versioning(Bucket=bucket).get("Status")
            expect(status == "Enabled", f"Status={status!r}, expected 'Enabled'")
            return "Status=Enabled"

        self.check("s3", "PutBucketVersioning", REQUIRED, versioning)
        self.check("s3", "GetBucketVersioning", REQUIRED, get_versioning)

        def put() -> str:
            resp = self.s3.put_object(
                Bucket=bucket, Key=key, Body=body, ContentType="application/json"
            )
            return f"VersionId={'yes' if resp.get('VersionId') else 'no'}"

        def get() -> str:
            got = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            expect(got == body, "body round-trip differs")
            return "body round-trips (UTF-8 Japanese)"

        def head() -> str:
            length = self.s3.head_object(Bucket=bucket, Key=key)["ContentLength"]
            expect(length == len(body), f"ContentLength={length}, expected {len(body)}")
            return ""

        self.check("s3", "PutObject", REQUIRED, put)
        self.check("s3", "GetObject", REQUIRED, get)
        self.check("s3", "HeadObject", REQUIRED, head)

        # What the Terraform aws_s3_bucket resource reads on every refresh.
        reads: list[tuple[str, Callable[[], Any], tuple[str, ...]]] = [
            ("GetBucketAcl", lambda: self.s3.get_bucket_acl(Bucket=bucket), ()),
            (
                "GetBucketPolicy",
                lambda: self.s3.get_bucket_policy(Bucket=bucket),
                ("NoSuchBucketPolicy",),
            ),
            (
                "GetBucketCors",
                lambda: self.s3.get_bucket_cors(Bucket=bucket),
                ("NoSuchCORSConfiguration",),
            ),
            (
                "GetBucketWebsite",
                lambda: self.s3.get_bucket_website(Bucket=bucket),
                ("NoSuchWebsiteConfiguration",),
            ),
            ("GetBucketLogging", lambda: self.s3.get_bucket_logging(Bucket=bucket), ()),
            (
                "GetBucketTagging",
                lambda: self.s3.get_bucket_tagging(Bucket=bucket),
                ("NoSuchTagSet",),
            ),
            (
                "GetBucketLifecycleConfiguration",
                lambda: self.s3.get_bucket_lifecycle_configuration(Bucket=bucket),
                ("NoSuchLifecycleConfiguration",),
            ),
            (
                "GetBucketReplication",
                lambda: self.s3.get_bucket_replication(Bucket=bucket),
                ("ReplicationConfigurationNotFoundError",),
            ),
            (
                "GetBucketEncryption",
                lambda: self.s3.get_bucket_encryption(Bucket=bucket),
                ("ServerSideEncryptionConfigurationNotFoundError",),
            ),
            (
                "GetBucketAccelerateConfiguration",
                lambda: self.s3.get_bucket_accelerate_configuration(Bucket=bucket),
                (),
            ),
            (
                "GetBucketRequestPayment",
                lambda: self.s3.get_bucket_request_payment(Bucket=bucket),
                (),
            ),
            (
                "GetBucketOwnershipControls",
                lambda: self.s3.get_bucket_ownership_controls(Bucket=bucket),
                ("OwnershipControlsNotFoundError",),
            ),
            (
                "GetPublicAccessBlock",
                lambda: self.s3.get_public_access_block(Bucket=bucket),
                ("NoSuchPublicAccessBlockConfiguration",),
            ),
            (
                "GetObjectLockConfiguration",
                lambda: self.s3.get_object_lock_configuration(Bucket=bucket),
                ("ObjectLockConfigurationNotFoundError",),
            ),
        ]
        for name, fn, ok_codes in reads:
            self.check(
                "s3",
                f"{name} (Terraform refresh)",
                REQUIRED,
                lambda fn=fn: fn() and None,
                ok_codes=ok_codes,
            )

        self.check(
            "s3",
            "DeleteObject",
            OPTIONAL,
            lambda: self.s3.delete_object(Bucket=bucket, Key=key) and None,
        )
        self.probe_object_lock()

    def probe_object_lock(self) -> None:
        bucket = f"cortex-probe-lock-{self.suffix}"
        key = "evidence/pack.json"
        until = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)

        if not self.check(
            "s3",
            "CreateBucket(ObjectLockEnabledForBucket=True)",
            OBJECT_LOCK,
            lambda: self.s3.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True) and None,
        ):
            return

        def put_lock_config() -> str:
            self.s3.put_object_lock_configuration(
                Bucket=bucket,
                ObjectLockConfiguration={
                    "ObjectLockEnabled": "Enabled",
                    "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 1}},
                },
            )
            return ""

        def get_lock_config() -> str:
            cfg = self.s3.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
            mode = cfg.get("Rule", {}).get("DefaultRetention", {}).get("Mode")
            expect(
                cfg.get("ObjectLockEnabled") == "Enabled",
                f"ObjectLockEnabled={cfg.get('ObjectLockEnabled')!r}",
            )
            expect(mode == "COMPLIANCE", f"DefaultRetention.Mode={mode!r}")
            return "Enabled, COMPLIANCE 1 day"

        version: dict[str, str] = {}

        def put_retained() -> str:
            resp = self.s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=b"{}",
                ObjectLockMode="COMPLIANCE",
                ObjectLockRetainUntilDate=until,
            )
            version_id = resp.get("VersionId")
            expect(bool(version_id), "no VersionId on an Object Lock bucket")
            version["id"] = str(version_id)
            return ""

        def get_retention() -> str:
            ret = self.s3.get_object_retention(Bucket=bucket, Key=key, VersionId=version["id"])[
                "Retention"
            ]
            expect(ret.get("Mode") == "COMPLIANCE", f"Mode={ret.get('Mode')!r}")
            return f"Mode=COMPLIANCE until {ret.get('RetainUntilDate')}"

        self.check("s3", "PutObjectLockConfiguration", OBJECT_LOCK, put_lock_config)
        self.check("s3", "GetObjectLockConfiguration", OBJECT_LOCK, get_lock_config)
        if not self.check("s3", "PutObject(ObjectLockMode=COMPLIANCE)", OBJECT_LOCK, put_retained):
            return
        self.check("s3", "GetObjectRetention", OBJECT_LOCK, get_retention)
        self.check(
            "s3",
            "DeleteObject(VersionId) under COMPLIANCE retention is refused",
            OBJECT_LOCK,
            lambda: self.s3.delete_object(Bucket=bucket, Key=key, VersionId=version["id"]) and None,
            expect_codes=("AccessDenied", "InvalidRequest", "ObjectLocked"),
        )

    # ── KMS ─────────────────────────────────────────────────────────────────

    def probe_kms(self) -> None:
        key: dict[str, Any] = {}
        alias = f"alias/cortex/probe/{self.suffix}"
        context = {"tenant_id": "00000000-0000-0000-0000-000000000001"}

        def create() -> str:
            key["id"] = self.kms.create_key(
                Description="cortex probe KEK",
                KeyUsage="ENCRYPT_DECRYPT",
                KeySpec="SYMMETRIC_DEFAULT",
            )["KeyMetadata"]["KeyId"]
            return ""

        if not self.check("kms", "CreateKey (no tags)", REQUIRED, create):
            return

        def describe(expected_state: str) -> Callable[[], str]:
            def run() -> str:
                state = self.kms.describe_key(KeyId=key["id"])["KeyMetadata"]["KeyState"]
                expect(state == expected_state, f"KeyState={state!r}, expected {expected_state!r}")
                return f"KeyState={state}"

            return run

        def list_aliases() -> str:
            names = [a["AliasName"] for a in self.kms.list_aliases(KeyId=key["id"])["Aliases"]]
            expect(alias in names, f"{alias} not listed")
            return ""

        def data_key() -> str:
            resp = self.kms.generate_data_key(
                KeyId=alias, KeySpec="AES_256", EncryptionContext=context
            )
            expect(
                len(resp["Plaintext"]) == 32,
                f"plaintext is {len(resp['Plaintext'])} bytes, expected 32",
            )
            key["dek_plain"], key["dek_blob"] = resp["Plaintext"], resp["CiphertextBlob"]
            return "32-byte DEK + wrapped blob"

        def unwrap() -> str:
            plain = self.kms.decrypt(CiphertextBlob=key["dek_blob"], EncryptionContext=context)[
                "Plaintext"
            ]
            expect(plain == key["dek_plain"], "unwrapped DEK differs")
            return "unwraps to the same DEK"

        def round_trip() -> str:
            blob = self.kms.encrypt(KeyId=key["id"], Plaintext=b"probe")["CiphertextBlob"]
            plain = self.kms.decrypt(CiphertextBlob=blob)["Plaintext"]
            expect(plain == b"probe", "Encrypt/Decrypt round-trip differs")
            return ""

        self.check("kms", "DescribeKey", REQUIRED, describe("Enabled"))
        self.check(
            "kms",
            "CreateAlias",
            REQUIRED,
            lambda: self.kms.create_alias(AliasName=alias, TargetKeyId=key["id"]) and None,
        )
        self.check("kms", "ListAliases", REQUIRED, list_aliases)
        if self.check("kms", "GenerateDataKey(AES_256, EncryptionContext)", REQUIRED, data_key):
            self.check("kms", "Decrypt (unwrap DEK)", REQUIRED, unwrap)
            self.check(
                "kms",
                "Decrypt with a different EncryptionContext is refused",
                OPTIONAL,
                lambda: (
                    self.kms.decrypt(
                        CiphertextBlob=key["dek_blob"],
                        EncryptionContext={"tenant_id": "someone-else"},
                    )
                    and None
                ),
                expect_codes=("InvalidCiphertextException",),
            )
        self.check("kms", "Encrypt + Decrypt", REQUIRED, round_trip)
        self.check(
            "kms",
            "GetKeyPolicy",
            OPTIONAL,
            lambda: self.kms.get_key_policy(KeyId=key["id"], PolicyName="default") and None,
        )
        self.check(
            "kms",
            "GetKeyRotationStatus",
            OPTIONAL,
            lambda: self.kms.get_key_rotation_status(KeyId=key["id"]) and None,
        )
        self.check(
            "kms",
            "ListResourceTags",
            OPTIONAL,
            lambda: self.kms.list_resource_tags(KeyId=key["id"]) and None,
        )
        self.check(
            "kms",
            "TagResource",
            EXPECTED_501,
            lambda: (
                self.kms.tag_resource(
                    KeyId=key["id"], Tags=[{"TagKey": "app", "TagValue": "cortex"}]
                )
                and None
            ),
        )

        # Crypto-shredding: after ScheduleKeyDeletion the wrapped DEK must not unwrap.
        self.check(
            "kms",
            "ScheduleKeyDeletion(7 days)",
            REQUIRED,
            lambda: self.kms.schedule_key_deletion(KeyId=key["id"], PendingWindowInDays=7) and None,
        )
        self.check(
            "kms", "DescribeKey after ScheduleKeyDeletion", REQUIRED, describe("PendingDeletion")
        )
        if "dek_blob" in key:
            self.check(
                "kms",
                "Decrypt after ScheduleKeyDeletion is refused (crypto-shred)",
                REQUIRED,
                lambda: (
                    self.kms.decrypt(CiphertextBlob=key["dek_blob"], EncryptionContext=context)
                    and None
                ),
                expect_codes=("KMSInvalidStateException",),
            )

        # Fallback if PendingDeletion did not block Decrypt: DisableKey.
        other: dict[str, Any] = {}

        def disable_then_decrypt() -> str:
            other["id"] = self.kms.create_key(Description="cortex probe disable")["KeyMetadata"][
                "KeyId"
            ]
            other["blob"] = self.kms.encrypt(KeyId=other["id"], Plaintext=b"x")["CiphertextBlob"]
            self.kms.disable_key(KeyId=other["id"])
            self.kms.decrypt(CiphertextBlob=other["blob"])
            return ""

        self.check(
            "kms",
            "Decrypt after DisableKey is refused",
            OPTIONAL,
            disable_then_decrypt,
            expect_codes=("DisabledException",),
        )

    # ── Secrets Manager ─────────────────────────────────────────────────────

    def probe_secrets(self) -> None:
        name = f"cortex/probe/{self.suffix}"

        if not self.check(
            "secretsmanager",
            "CreateSecret",
            REQUIRED,
            lambda: self.secrets.create_secret(Name=name, SecretString='{"v": 1}') and None,
        ):
            return

        def latest() -> str:
            value = self.secrets.get_secret_value(SecretId=name)["SecretString"]
            expect(value == '{"v": 2}', f"SecretString={value!r}, expected the PutSecretValue one")
            return "returns the latest version"

        self.check(
            "secretsmanager",
            "DescribeSecret",
            REQUIRED,
            lambda: self.secrets.describe_secret(SecretId=name) and None,
        )
        self.check(
            "secretsmanager",
            "GetResourcePolicy (Terraform refresh)",
            REQUIRED,
            lambda: self.secrets.get_resource_policy(SecretId=name) and None,
        )
        self.check(
            "secretsmanager",
            "PutSecretValue",
            REQUIRED,
            lambda: self.secrets.put_secret_value(SecretId=name, SecretString='{"v": 2}') and None,
        )
        self.check("secretsmanager", "GetSecretValue", REQUIRED, latest)
        self.check(
            "secretsmanager",
            "DeleteSecret(ForceDeleteWithoutRecovery)",
            OPTIONAL,
            lambda: (
                self.secrets.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True) and None
            ),
        )

    # ── STS ─────────────────────────────────────────────────────────────────

    def probe_sts(self) -> None:
        def identity() -> str:
            return f"Account={self.sts.get_caller_identity()['Account']}"

        self.check("sts", "GetCallerIdentity", OPTIONAL, identity)


def inspect_tls(endpoint: str, ca_file: str) -> dict[str, Any]:
    """Which names the emulator's certificate is valid for (undocumented)."""
    host_port = endpoint.split("://", 1)[1]
    host, _, port = host_port.partition(":")
    ctx = ssl.create_default_context(cafile=ca_file)
    ctx.check_hostname = False  # we only fetch the certificate here; checks follow
    with (
        socket.create_connection((host, int(port or 443)), timeout=5) as sock,
        ctx.wrap_socket(sock, server_hostname=host) as tls,
    ):
        der = tls.getpeercert(binary_form=True)
    expect(der is not None, "no peer certificate")
    cert = x509.load_der_x509_certificate(der or b"")
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = san.get_values_for_type(x509.DNSName)
        ip_names = [str(ip) for ip in san.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        dns_names, ip_names = [], []

    verified: dict[str, bool] = {}
    for name in ("127.0.0.1", "localhost", "aws-emulator"):
        strict = ssl.create_default_context(cafile=ca_file)
        try:
            with (
                socket.create_connection((host, int(port or 443)), timeout=5) as sock,
                strict.wrap_socket(sock, server_hostname=name),
            ):
                verified[name] = True
        except ssl.SSLCertVerificationError:
            verified[name] = False
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "dns": dns_names,
        "ip": ip_names,
        "verified": verified,
    }


def access_key_fingerprint(credentials_file: str) -> str:
    """sha256 prefix of the access key id: proves rotation without writing keys down."""
    for line in Path(credentials_file).read_text().splitlines():
        if line.strip().startswith("aws_access_key_id"):
            key_id = line.split("=", 1)[1].strip()
            return hashlib.sha256(key_id.encode()).hexdigest()[:12]
    return "unknown"


def render(results: list[Result], tls: dict[str, Any] | str, key_fpr: str) -> tuple[str, bool]:
    version = os.environ.get("EMULATOR_VERSION", "unknown")
    required_failures = [r for r in results if r.group == REQUIRED and r.outcome != "ok"]
    lock_gaps = [r for r in results if r.group == OBJECT_LOCK and r.outcome != "ok"]
    gate = not required_failures
    icon = {"ok": "ok", "not-implemented": "**501**", "failed": "**FAILED**"}

    run_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S")
    sha_arm64 = os.environ.get("EMULATOR_SHA256_ARM64", "?")
    sha_amd64 = os.environ.get("EMULATOR_SHA256_AMD64", "?")
    endpoint = os.environ.get("AWS_ENDPOINT_URL", "?")
    lines = [
        f"# AWS Emulator fidelity probe — {version}",
        "",
        "Generated by `make probe-emulator` (`services/org-brain-service/scripts/aws_probe.py`). "
        "Do not edit by hand; rerun the probe instead.",
        "",
        f"- Run at: {run_at} UTC",
        f"- Emulator package: `{version}` · sha256 arm64 `{sha_arm64}` · amd64 `{sha_amd64}`",
        f"- Endpoint: `{endpoint}` (TLS verification on, CA from the shared volume)",
        f"- Access key id fingerprint (sha256, first 12): `{key_fpr}` "
        "— changes on every emulator start",
        f"- **Gate: {'PASS' if gate else 'FAIL'}** — "
        f"{len(required_failures)} required operation(s) failing; "
        f"{len(lock_gaps)} Object Lock gap(s)",
        "",
    ]
    for group in GROUPS:
        rows = [r for r in results if r.group == group]
        if not rows:
            continue
        lines += [
            f"## {group}",
            "",
            "| Service | Operation | Result | Detail |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| {r.service} | {r.operation} | {icon[r.outcome]} | {r.detail.replace('|', '/')} |"
            for r in rows
        ]
        lines.append("")

    lines += ["## TLS certificate", ""]
    if isinstance(tls, str):
        lines.append(f"Could not inspect: {tls}")
    else:
        lines += [
            f"- Subject: `{tls['subject']}` · Issuer: `{tls['issuer']}` "
            f"· Not after: `{tls['not_after']}`",
            f"- SAN DNS names: {', '.join(f'`{n}`' for n in tls['dns']) or 'none'}",
            f"- SAN IP addresses: {', '.join(f'`{n}`' for n in tls['ip']) or 'none'}",
            "- Hostname verification: "
            + ", ".join(
                f"`{n}` {'passes' if ok else 'fails'}" for n, ok in tls["verified"].items()
            ),
        ]
    lines.append("")
    return "\n".join(lines), gate


def main() -> int:
    parser = argparse.ArgumentParser(description="AWS Emulator fidelity probe")
    parser.add_argument("--out-dir", default=".", help="where to write emulator-probe-<version>.md")
    args = parser.parse_args()

    probe = Probe()
    probe.probe_s3()
    probe.probe_kms()
    probe.probe_secrets()
    probe.probe_sts()

    try:
        tls: dict[str, Any] | str = inspect_tls(
            os.environ["AWS_ENDPOINT_URL"], os.environ["AWS_CA_BUNDLE"]
        )
    except (OSError, KeyError, ValueError, ProbeAssertionError) as exc:
        tls = f"{type(exc).__name__}: {exc}"

    report, gate = render(
        probe.results,
        tls,
        access_key_fingerprint(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", "")),
    )
    out = Path(args.out_dir) / f"emulator-probe-{os.environ.get('EMULATOR_VERSION', 'unknown')}.md"
    out.write_text(report)

    for r in probe.results:
        print(f"[{r.group:<12}] {r.outcome:<15} {r.service:<14} {r.operation}  {r.detail}")
    print(f"\nGate: {'PASS' if gate else 'FAIL'} — report written to {out}")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
