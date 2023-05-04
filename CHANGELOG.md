Changelog
=========


0.3.2 (unreleased)
------------------

- Add `get_field_type_map` to `ServiceDirective`. Since Sphinx 4.0 it is required.
  See https://github.com/sphinx-doc/sphinx/pull/7416


0.3.1 (2018-04-21)
------------------

- Add custom untested changes. The goal is to merge them upstream in chunks.
  - Add `ignore-methods` option
  - Add `title-replace` option
  - Add `exec` directive

- Add `docstring-replace` option to `cornice-autodoc` directive.
  You can use the new option like this:

  ```
  .. cornice-autodoc::
    :modules: myapp.api
    :service: ping
    :docstring-replace: {
        "Foo Bar": "John Smith",
        "http://localhost:8080": "https://myapp.com"
      }
  ```

  This will replace "Foo Bar" with "John Smith" and "http://localhost:8080"
  with "https://myapp.com" from the docstring.

- Start using CHANGELOG.md
