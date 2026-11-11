# Database CA certificates

`aws-rds-global-bundle.pem` is the Amazon RDS global CA bundle: the root CAs (RSA2048, RSA4096 and ECC384, G1, every
region) that sign Amazon RDS and Aurora server certificates. The image copies it to
`/etc/alchemist/certs/aws-rds-global-bundle.pem`, and the AWS stack sets `ALC_DB_SSLROOTCERT` to that path with
`ALC_DB_SSLMODE=verify-full`, so the app only talks to a database whose certificate one of these CAs signed and whose
name matches the cluster endpoint.

It is kept in the repository rather than downloaded at build time so the build needs no internet and a change to the
trust list shows up in review. The earliest of these CAs expires in 2061. To refresh it:

```sh
curl -sSf https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem -o deploy/certs/aws-rds-global-bundle.pem
```

Fetched 2026-11-11 (114 certificates, sha256 `90a6f1a7978a1b1b096dca6c57511ca48d3b8cf052f8e704baf358f34ea2f21b`).

Other databases (on-prem, Azure, Cloud SQL): leave this file alone and point `ALC_DB_SSLROOTCERT` at your own CA, or
use the system trust store (the default when `ALC_DB_SSLROOTCERT` is unset). See the Helm chart's `database` values.
