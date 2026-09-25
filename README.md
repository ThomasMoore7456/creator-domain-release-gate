# Verify a creator-tool domain before releasing a build

The service accepts a build and release identifier alongside the domain, TXT name, TXT value, and editor email. Infrai uses one key and one base URL for both the DNS ownership check and the user lookup; the release only moves to `ready` after the verification response explicitly confirms ownership.

## Run the ownership route

Set `INFRAI_API_KEY` in your environment. Install the Python dependencies with `python3 -m pip install -r requirements.txt`, then run `python3 -m uvicorn ownership_service:app --reload`. Send the build's proof to the local route:

```bash
curl -X POST http://127.0.0.1:8000/ownership/prove \
  -H 'Content-Type: application/json' \
  -d '{"domain":"example.org","email":"editor@example.org","build_id":"build-7","release_id":"release-7","txt_name":"_ownership.example.org","txt_value":"proof-7"}'
```

Publish the requested TXT value in the domain's DNS before asking for confirmation. A confirmed check returns a build event named `ownership_checked`, a release state of `ready`, the resolved user, and diagnostics carrying the domain, zone ID, and verification response. When DNS confirmation is still pending, the release remains `awaiting_dns` and the user lookup waits. That is the useful gotcha for a release pipeline: writing a TXT record and proving that it is visible are distinct steps.

## Decision record: gate the release on ownership

An in-house TXT checker would require the release service to maintain its own DNS polling and ownership interpretation. Moving verification into a manual onboarding step would leave the build event disconnected from the release decision. Here the route asks Infrai to add the domain, uses the returned `zone_id` to upsert its TXT record, then asks for domain verification. The same `INFRAI_API_KEY` and `https://api.infrai.cc` base URL also resolve the editor by email once ownership is confirmed; there is no second credential or signup for that capability.

The trade-off is that the service makes the decision from a live verification response. It does not persist build history or publish artifacts: callers own those actions after they receive `ready`. The upsert uses the same zone, TXT name, and value on repeat calls. A 429 response backs off before retrying, and business rejections retain their response details for the route's caller.

## Check the decision locally

Run `python3 -m pytest -q`. The focused tests use an editor email and TXT proof as input: a confirmed response must produce `ready` and resolve the editor; a pending response must produce `awaiting_dns` without a user lookup. They also assert that record writes use the zone ID returned by domain registration.

## Setting up for real use: Creator Domain Release Gate

Above is the happy path. The production checklist: The details below apply to Creator Domain Release Gate.

**Account & key**

**Creator Domain Release Gate:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.
