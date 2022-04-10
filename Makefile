.PHONY: tests
.DEFAULT_GOAL := tests

dev:
	poetry install

tests:
	python -m pytest -vv

coverage:
	coverage run --include=epy.py -m pytest -vv tests
	coverage html
	python -m http.server -d htmlcov

release:
	python setup.py sdist bdist_wheel
	twine upload --skip-existing dist/*
