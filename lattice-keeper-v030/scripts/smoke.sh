#!/usr/bin/env bash
# End-to-end smoke test against the running compose stack.
set -uo pipefail
cd "$(dirname "$0")/.."
KEY=$(grep '^LATTICE_API_KEY=' .env | cut -d= -f2-)
URL=${URL:-http://127.0.0.1:8765}
H=(-H "Authorization: Bearer $KEY" -H 'Content-Type: application/json')
pass=0; fail=0
check(){ if [ "$2" = "$3" ]; then echo "PASS $1 ($2)"; pass=$((pass+1)); else echo "FAIL $1: got '$2' want '$3'"; fail=$((fail+1)); fi; }
j(){ python3 -c "import json,sys;d=json.load(sys.stdin);print($1)" 2>/dev/null || echo "<bad response>"; }

check "health" "$(curl -s $URL/health | j 'd["status"],d["redis"],d["pqc"]')" "ok ok ml-dsa-65+ml-kem-768"
check "pubkeys" "$(curl -s $URL/pubkeys | j 'd["pqc"],d["keys"]["ml_dsa_65"]["alg"],d["keys"]["ml_kem_768"]["alg"]')" "available ML-DSA-65 ML-KEM-768"
check "auth none" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/verify -H 'Content-Type: application/json' -d '{}')" "401"
check "auth wrong" "$(curl -s -o /dev/null -w '%{http_code}' $URL/audit/verify -H "Authorization: Bearer ${KEY}x")" "401"

BODY='{"control_variable":"Canadian Wildlife Conservation Report 2026 (CRC-2026-001)","report_id":"CRC-2026-001","valuation_cad":3100000000.00,"attributes":{"region":"BC"}}'
curl -s -X POST $URL/rwa/tokenize "${H[@]}" -d "$BODY" > /tmp/smoke_tok.json
check "tokenize" "$(j 'd["status"],d["pqc"],d["encryption"]["status"],d["reference_check"]["status"]' < /tmp/smoke_tok.json)" "tokenized ml-dsa-65 encrypted passed"
TID=$(j 'd["token_id"]' < /tmp/smoke_tok.json); 
check "tokenize mismatch" "$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/rwa/tokenize "${H[@]}" -d '{"report_id":"WRONG","valuation_cad":1}')" "403"
check "get record" "$(curl -s $URL/rwa/$TID "${H[@]}" | j 'd["token_id"]')" "$TID"

python3 -c "import json;d=json.load(open('/tmp/smoke_tok.json'));print(json.dumps({'payload':d['payload'],'signature':d['signature']}))" > /tmp/smoke_v.json
check "verify valid" "$(curl -s -X POST $URL/verify "${H[@]}" -d @/tmp/smoke_v.json | j 'd["valid"],d["ed25519"],d["ml_dsa_65"]')" "True True True"
python3 -c "import json;d=json.load(open('/tmp/smoke_v.json'));d['payload']['valuation_cad']='9999999999.00';print(json.dumps(d))" > /tmp/smoke_vt.json
check "verify tampered" "$(curl -s -X POST $URL/verify "${H[@]}" -d @/tmp/smoke_vt.json | j 'd["valid"]')" "False"
python3 -c "import json;d=json.load(open('/tmp/smoke_v.json'));d['signature']['ml_dsa_65']=None;print(json.dumps(d))" > /tmp/smoke_vs.json
check "verify pqc-stripped" "$(curl -s -X POST $URL/verify "${H[@]}" -d @/tmp/smoke_vs.json | j 'd["valid"]')" "False"
check "policy get" "$(curl -s $URL/policy "${H[@]}" | j 'd["policy"]["format"],d["version"]>=1')" "lattice-keeper-policy/v1 True"
check "policy unsigned put" "$(curl -s $URL/policy "${H[@]}" | python3 -c "import json,sys;d=json.load(sys.stdin);p=d['policy'];p['version']+=1;print(json.dumps({'policy':p,'signature':{}}))" | curl -s -o /dev/null -w '%{http_code}' -X PUT $URL/policy "${H[@]}" -d @-)" "$(curl -s $URL/health | j '"403" if d["operator_key"]=="pinned" else "409"')"
check "policy put no auth" "$(curl -s -o /dev/null -w '%{http_code}' -X PUT $URL/policy -H 'Content-Type: application/json' -d '{}')" "401"
check "beta_global gone" "$(curl -s -o /dev/null -w '%{http_code}' -X PUT $URL/telemetry/beta_global "${H[@]}" -d '{"beta_global":1.0}')" "410"
check "audit verify" "$(curl -s $URL/audit/verify "${H[@]}" | j 'd["valid"]')" "True"
echo "audit: $(curl -s $URL/audit/verify "${H[@]}")"
echo "pass=$pass fail=$fail"; [ "$fail" -eq 0 ]
