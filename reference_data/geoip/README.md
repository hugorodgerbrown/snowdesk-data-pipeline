# GeoIP database

`GeoLite2-City.mmdb` is a free IP-to-city database published by MaxMind.
It is **not committed to git** — the `.mmdb` binary is in `.gitignore`.

## Source

Downloaded from the MaxMind GeoLite2 free tier:
<https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/>

A MaxMind account is required to download the file. The licence is the
Creative Commons Attribution-ShareAlike 4.0 International licence —
attribution is surfaced via the Snowdesk colophon page.

## How to get the database

### Local development

1. Sign up for a free MaxMind account at <https://www.maxmind.com/en/geolite2/signup>
2. Generate a licence key at <https://www.maxmind.com/en/accounts/current/license-key>
3. Add to your `.env`:
   ```
   MAXMIND_ACCOUNT_ID=<your numeric account ID>
   MAXMIND_LICENSE_KEY=<your licence key>
   ```
4. Run the download script:
   ```bash
   ./bin/fetch-geoip-data
   ```

If you do not set the credentials, local dev still boots — GeoIP fields will
simply be empty strings / null. Geo capture only works in environments where
`MAXMIND_ACCOUNT_ID` and `MAXMIND_LICENSE_KEY` are configured.

### Production (Render)

`build.sh` calls `./bin/fetch-geoip-data` automatically during every deploy.
Set `MAXMIND_ACCOUNT_ID` and `MAXMIND_LICENSE_KEY` in the Render environment
variables dashboard. The script is idempotent and exits 0 even if MaxMind is
temporarily unreachable as long as a stale database already exists on disk.

## Refresh cadence

MaxMind publishes a new GeoLite2-City build roughly every two weeks.
Production refreshes automatically on every deploy. For local dev, re-run
`./bin/fetch-geoip-data` when you want a fresher database.
