.PHONY: all dist release-check test-release test-emit pin verify-drop handoff-check repair schemas examples corpus runs test lint artifacts verify ledger ledger-verify rules clean

all: repair ledger schemas examples corpus runs test lint artifacts verify ledger-verify

repair:
	python3 repair_pass.py key.yaml key.repaired.yaml

ledger:
	rm -rf ledger
	python3 sk_ledger.py seed ledger/solomons-key-builder-ledger.jsonl --from-key key.repaired.yaml
	python3 sk_ledger.py append ledger/solomons-key-builder-ledger.jsonl \
		--pass RUN_build_0001 --name "Demo governed build run" --actor Codex \
		--actor-role builder --type validation_run --result pass \
		--note "Witnesses the run in runs/good."
	python3 sk_ledger.py head ledger/solomons-key-builder-ledger.jsonl > ledger/HEAD

schemas:
	python3 gen_artifact_schemas.py key.repaired.yaml schemas/artifacts

examples:
	python3 gen_artifact_examples.py key.repaired.yaml examples

corpus:
	python3 gen_redcorpus.py key.repaired.yaml redcorpus

runs:
	python3 gen_runs.py key.repaired.yaml ledger/solomons-key-builder-ledger.jsonl runs

test:
	python3 test_sk_lint.py
	python3 test_sk_ledger.py
	python3 test_sk_artifacts.py
	python3 test_sk_verify.py

test-emit:
	python3 test_sk_emit.py

lint:
	python3 sk_lint.py key.repaired.yaml

artifacts:
	python3 sk_artifacts.py validate --dir examples/valid \
		--key key.repaired.yaml --ledger ledger/solomons-key-builder-ledger.jsonl

verify:
	python3 sk_verify.py runs/good

ledger-verify:
	python3 sk_ledger.py verify ledger/solomons-key-builder-ledger.jsonl \
		--expect-head $$(cat ledger/HEAD)

dist:
	python3 build.py dist

release-check:
	python3 build.py release-check

test-release:
	python3 build.py test-release

verify-drop:
	python3 sk_handoff.py verify-drop

pin:
	python3 sk_handoff.py pin

handoff-check:
	python3 sk_handoff.py check

rules:
	python3 sk_lint.py --rules

clean:
	rm -rf redcorpus schemas examples runs __pycache__
