# GeoIP database

`GeoLite2-Country.mmdb` is a free IP-to-country database published by MaxMind.

## Source

Downloaded from the MaxMind GeoLite2 free tier:
<https://dev.maxmind.com/geoip/geolite2-free-geolocation-data/>

A MaxMind account is required to download the file. The licence is the
Creative Commons Attribution-ShareAlike 4.0 International licence —
attribution is surfaced via the Snowdesk colophon page.

## Refresh cadence

MaxMind publishes a new GeoLite2-Country build roughly every two weeks.
Refresh quarterly (or whenever a mis-classified country is noticed):

1. Log in at <https://www.maxmind.com/en/accounts/current/geoip/downloads>
2. Download **GeoLite2-Country** (MaxMind DB binary, .tar.gz)
3. Extract `GeoLite2-Country.mmdb` and replace this file.
4. Commit with message: `chore: refresh GeoLite2-Country.mmdb (YYYY-MM-DD)`

## File size

~9 MB binary. Committed directly — no LFS required at this size.
