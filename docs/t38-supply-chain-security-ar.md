# T38 — Supply Chain Security

workflow مستقل Supply Chain Security يعمل على push/PR/main ويدعم manual dispatch.

البوابات: pip-audit على requirements.lock لفحص الثغرات، gitleaks للsecret scanning، Bandit static security analysis، Anchore SBOM بصيغة SPDX JSON، SHA-256 release checksum manifest، وGitHub artifact attestation للـSBOM مقابل requirements.lock عبر OIDC.

Sigstore مناسب هنا عبر GitHub artifact attestations المبنية على Sigstore transparency/identity model بدل إدارة signing key محلي داخل المستودع. permissions محدودة إلى contents:read مع id-token/attestations المطلوبة فقط.

النواتج sbom.spdx.json وrelease-checksums.txt تحفظ كartifact لتدقيق release.
