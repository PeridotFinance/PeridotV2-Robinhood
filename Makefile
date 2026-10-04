CONTRACTS := contracts/robinhood-vaults
.PHONY: verify build test test-python reproduce fork-vault fork-margin frontend

.PHONY: test-remediation fork-remediation
test-remediation:
	forge fmt --check remediation/src remediation/script remediation/test
	forge test --no-match-path '*/fork/*' --match-contract 'LendingPriceAdapterTest|VaultRecoveryTest|VaultV2|RobinhoodBoostedVaultV2CompatibilityTest|VaultPostExitExposureTest'
	python3 remediation/tools/verify_artifact.py
	python3 remediation/tools/verify_vault_layout.py
	FOUNDRY_PROFILE=vault_upgrade forge build
	python3 remediation/tools/verify_vault_artifact.py
	python3 -m unittest discover -s remediation/tools -p 'test_*.py'

# Lending-delegate rounding candidate. Needs the dedicated profile: the candidate does not fit
# EIP-170 under the default or lending_upgrade settings. See remediation/README.md.
test-lending-candidate:
	forge fmt --check remediation/src remediation/script remediation/test/LendingRoundingVault.t.sol remediation/test/LendingMintRouter.t.sol remediation/test/fork/LendingDelegateUpgradeMainnet.t.sol remediation/test/fork/LendingDelegateMarginCompat.t.sol remediation/test/fork/LendingDelegateMintRouter.t.sol
	FOUNDRY_PROFILE=lending_candidate forge test --match-path 'remediation/test/Lending*.t.sol'
	FOUNDRY_PROFILE=lending_upgrade forge build
	FOUNDRY_PROFILE=lending_candidate forge build --sizes --skip 'remediation/test/**' --skip 'remediation/script/**'
	python3 remediation/tools/verify_lending_delegate_candidate.py
	python3 -m unittest discover -s remediation/tools -p 'test_verify_lending*.py'

# Pre-install rehearsal only. The correction was installed on October 2, 2026 (blocks 78,176,131 and
# 78,176,155), and these tests assert the original delegate codehash, so against current chain state
# they stop at that assertion by design. Rerun only against an earlier block on an archive RPC.
fork-lending-candidate:
	python3 remediation/tools/lending_delegate_upgrade_rehearsal.py

fork-remediation:
	python3 remediation/tools/fork.py

verify:
	python3 tools/verify_snapshot.py

build: verify
	cd $(CONTRACTS) && forge build
	cd $(CONTRACTS) && FOUNDRY_PROFILE=margin_mainnet forge build

test: verify
	cd $(CONTRACTS) && forge test --no-match-path 'test/fork/*'
	$(MAKE) test-python

test-python:
	python3 -m unittest discover -s tools -p 'test_*.py'
	cd $(CONTRACTS)/margin-mainnet/tools && python3 -m unittest discover -p 'test_*.py'
	cd $(CONTRACTS)/margin-mainnet/five-x && python3 -m unittest discover -p 'test_*.py'
	cd $(CONTRACTS)/margin-mainnet/keeper-service && python3 -m unittest discover -p 'test_*.py'

reproduce: verify
	python3 tools/verify_snapshot.py --compile

fork-vault:
	@test -n "$$ROBINHOOD_RPC_URL" || (echo 'Set ROBINHOOD_RPC_URL to an archive-capable Robinhood Chain RPC'; exit 1)
	cd $(CONTRACTS) && forge test --match-path 'test/fork/*'

fork-margin:
	@test -n "$$ROBINHOOD_RPC_URL" || (echo 'Set ROBINHOOD_RPC_URL to an archive-capable Robinhood Chain RPC'; exit 1)
	cd $(CONTRACTS) && FOUNDRY_PROFILE=margin_mainnet forge test

frontend:
	@echo 'The frontend is being developed separately. See frontend/README.md for integration notes.'
	@exit 1
