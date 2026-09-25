CONTRACTS := contracts/robinhood-vaults
.PHONY: verify build test test-python reproduce fork-vault fork-margin frontend

verify:
	python3 tools/verify_snapshot.py

build: verify
	cd $(CONTRACTS) && forge build
	cd $(CONTRACTS) && FOUNDRY_PROFILE=margin_mainnet forge build

test: verify
	cd $(CONTRACTS) && forge test --no-match-path 'test/fork/*'
	$(MAKE) test-python

test-python:
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
	@echo 'The frontend is being developed separately. See frontend/README.md for integration requirements.'
	@exit 1
