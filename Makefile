.PHONY: tests
.DEFAULT_GOAL := tests

requirements:
	poetry install --no-root

tests:
	python -m pytest -vv

release:
	python setup.py sdist bdist_wheel
	twine upload --skip-existing dist/*
