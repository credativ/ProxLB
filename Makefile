#!/usr/bin/make -f

build:
	poetry build

install:
	poetry install --all-extras

test: install
	poetry run py.test --mypy --pycodestyle --flake8 --cache-clear -vv
	poetry run pyright --verbose

clean:
	poetry env remove --all
