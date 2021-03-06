import ast
import keyword
import re
from typing import TYPE_CHECKING, Any, Dict, Iterable, List

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    PathCompleter,
)
from prompt_toolkit.contrib.regular_languages.compiler import compile as compile_grammar
from prompt_toolkit.contrib.regular_languages.completion import GrammarCompleter
from prompt_toolkit.document import Document

from ptpython.utils import get_jedi_script_from_document

if TYPE_CHECKING:
    from prompt_toolkit.contrib.regular_languages.compiler import _CompiledGrammar

__all__ = ["PythonCompleter"]


class PythonCompleter(Completer):
    """
    Completer for Python code.
    """

    def __init__(self, get_globals, get_locals, get_enable_dictionary_completion):
        super().__init__()

        self.get_globals = get_globals
        self.get_locals = get_locals
        self.get_enable_dictionary_completion = get_enable_dictionary_completion

        self.dictionary_completer = DictionaryCompleter(get_globals, get_locals)

        self._path_completer_cache = None
        self._path_completer_grammar_cache = None

    @property
    def _path_completer(self) -> GrammarCompleter:
        if self._path_completer_cache is None:
            self._path_completer_cache = GrammarCompleter(
                self._path_completer_grammar,
                {
                    "var1": PathCompleter(expanduser=True),
                    "var2": PathCompleter(expanduser=True),
                },
            )
        return self._path_completer_cache

    @property
    def _path_completer_grammar(self) -> "_CompiledGrammar":
        """
        Return the grammar for matching paths inside strings inside Python
        code.
        """
        # We make this lazy, because it delays startup time a little bit.
        # This way, the grammar is build during the first completion.
        if self._path_completer_grammar_cache is None:
            self._path_completer_grammar_cache = self._create_path_completer_grammar()
        return self._path_completer_grammar_cache

    def _create_path_completer_grammar(self) -> "_CompiledGrammar":
        def unwrapper(text: str) -> str:
            return re.sub(r"\\(.)", r"\1", text)

        def single_quoted_wrapper(text: str) -> str:
            return text.replace("\\", "\\\\").replace("'", "\\'")

        def double_quoted_wrapper(text: str) -> str:
            return text.replace("\\", "\\\\").replace('"', '\\"')

        grammar = r"""
                # Text before the current string.
                (
                    [^'"#]                                  |  # Not quoted characters.
                    '''  ([^'\\]|'(?!')|''(?!')|\\.])*  ''' |  # Inside single quoted triple strings
                    "" " ([^"\\]|"(?!")|""(?!^)|\\.])* "" " |  # Inside double quoted triple strings

                    \#[^\n]*(\n|$)           |  # Comment.
                    "(?!"") ([^"\\]|\\.)*"   |  # Inside double quoted strings.
                    '(?!'') ([^'\\]|\\.)*'      # Inside single quoted strings.

                        # Warning: The negative lookahead in the above two
                        #          statements is important. If we drop that,
                        #          then the regex will try to interpret every
                        #          triple quoted string also as a single quoted
                        #          string, making this exponentially expensive to
                        #          execute!
                )*
                # The current string that we're completing.
                (
                    ' (?P<var1>([^\n'\\]|\\.)*) |  # Inside a single quoted string.
                    " (?P<var2>([^\n"\\]|\\.)*)    # Inside a double quoted string.
                )
        """

        return compile_grammar(
            grammar,
            escape_funcs={"var1": single_quoted_wrapper, "var2": double_quoted_wrapper},
            unescape_funcs={"var1": unwrapper, "var2": unwrapper},
        )

    def _complete_path_while_typing(self, document: Document) -> bool:
        char_before_cursor = document.char_before_cursor
        return bool(
            document.text
            and (char_before_cursor.isalnum() or char_before_cursor in "/.~")
        )

    def _complete_python_while_typing(self, document: Document) -> bool:
        char_before_cursor = document.char_before_cursor
        return bool(
            document.text
            and (char_before_cursor.isalnum() or char_before_cursor in "_.")
        )

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        """
        Get Python completions.
        """
        # Do dictionary key completions.
        if self.get_enable_dictionary_completion():
            has_dict_completions = False
            for c in self.dictionary_completer.get_completions(
                document, complete_event
            ):
                if c.text not in "[.":
                    # If we get the [ or . completion, still include the other
                    # completions.
                    has_dict_completions = True
                yield c
            if has_dict_completions:
                return

        # Do Path completions (if there were no dictionary completions).
        if complete_event.completion_requested or self._complete_path_while_typing(
            document
        ):
            for c in self._path_completer.get_completions(document, complete_event):
                yield c

        # If we are inside a string, Don't do Jedi completion.
        if self._path_completer_grammar.match(document.text_before_cursor):
            return

        # Do Jedi Python completions.
        if complete_event.completion_requested or self._complete_python_while_typing(
            document
        ):
            script = get_jedi_script_from_document(
                document, self.get_locals(), self.get_globals()
            )

            if script:
                try:
                    completions = script.completions()
                except TypeError:
                    # Issue #9: bad syntax causes completions() to fail in jedi.
                    # https://github.com/jonathanslenders/python-prompt-toolkit/issues/9
                    pass
                except UnicodeDecodeError:
                    # Issue #43: UnicodeDecodeError on OpenBSD
                    # https://github.com/jonathanslenders/python-prompt-toolkit/issues/43
                    pass
                except AttributeError:
                    # Jedi issue #513: https://github.com/davidhalter/jedi/issues/513
                    pass
                except ValueError:
                    # Jedi issue: "ValueError: invalid \x escape"
                    pass
                except KeyError:
                    # Jedi issue: "KeyError: u'a_lambda'."
                    # https://github.com/jonathanslenders/ptpython/issues/89
                    pass
                except IOError:
                    # Jedi issue: "IOError: No such file or directory."
                    # https://github.com/jonathanslenders/ptpython/issues/71
                    pass
                except AssertionError:
                    # In jedi.parser.__init__.py: 227, in remove_last_newline,
                    # the assertion "newline.value.endswith('\n')" can fail.
                    pass
                except SystemError:
                    # In jedi.api.helpers.py: 144, in get_stack_at_position
                    # raise SystemError("This really shouldn't happen. There's a bug in Jedi.")
                    pass
                except NotImplementedError:
                    # See: https://github.com/jonathanslenders/ptpython/issues/223
                    pass
                except Exception:
                    # Supress all other Jedi exceptions.
                    pass
                else:
                    for c in completions:
                        yield Completion(
                            c.name_with_symbols,
                            len(c.complete) - len(c.name_with_symbols),
                            display=c.name_with_symbols,
                            style=_get_style_for_name(c.name_with_symbols),
                        )


class DictionaryCompleter(Completer):
    """
    Experimental completer for Python dictionary keys.

    Warning: This does an `eval` and `repr` on some Python expressions before
             the cursor, which is potentially dangerous. It doesn't match on
             function calls, so it only triggers attribute access.
    """

    def __init__(self, get_globals, get_locals):
        super().__init__()

        self.get_globals = get_globals
        self.get_locals = get_locals

        # Pattern for expressions that are "safe" to eval for auto-completion.
        # These are expressions that contain only attribute and index lookups.
        varname = r"[a-zA-Z_][a-zA-Z0-9_]*"

        expression = rf"""
            # Any expression safe enough to eval while typing.
            # No operators, except dot, and only other dict lookups.
            # Technically, this can be unsafe of course, if bad code runs
            # in `__getattr__` or ``__getitem__``.
            (
                # Variable name
                {varname}

                \s*

                (?:
                    # Attribute access.
                    \s* \. \s* {varname} \s*

                    |

                    # Item lookup.
                    # (We match the square brackets. The key can be anything.
                    # We don't care about matching quotes here in the regex.
                    # Nested square brackets are not supported.)
                    \s* \[ [^\[\]]+ \] \s*
                )*
            )
        """

        # Pattern for recognizing for-loops, so that we can provide
        # autocompletion on the iterator of the for-loop. (According to the
        # first item of the collection we're iterating over.)
        self.for_loop_pattern = re.compile(
            rf"""
                for \s+ ([a-zA-Z0-9_]+) \s+ in \s+ {expression} \s* :
            """,
            re.VERBOSE,
        )

        # Pattern for matching a simple expression (for completing [ or .
        # operators).
        self.expression_pattern = re.compile(
            rf"""
                {expression}
                $
            """,
            re.VERBOSE,
        )

        # Pattern for matching item lookups.
        self.item_lookup_pattern = re.compile(
            rf"""
                {expression}

                # Dict loopup to complete (square bracket open + start of
                # string).
                \[
                \s* ([^\[\]]*)$
            """,
            re.VERBOSE,
        )

        # Pattern for matching attribute lookups.
        self.attribute_lookup_pattern = re.compile(
            rf"""
                {expression}

                # Attribute loopup to complete (dot + varname).
                \.
                \s* ([a-zA-Z0-9_]*)$
            """,
            re.VERBOSE,
        )

    def _lookup(self, expression: str, temp_locals: Dict[str, Any]) -> object:
        """
        Do lookup of `object_var` in the context.
        `temp_locals` is a dictionary, used for the locals.
        """
        try:
            return eval(expression.strip(), self.get_globals(), temp_locals)
        except BaseException:
            return None  # Many exception, like NameError can be thrown here.

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:

        # First, find all for-loops, and assing the first item of the
        # collections they're iterating to the iterator variable, so that we
        # can provide code completion on the iterators.
        temp_locals = self.get_locals().copy()

        for match in self.for_loop_pattern.finditer(document.text_before_cursor):
            varname, expression = match.groups()
            expression_val = self._lookup(expression, temp_locals)

            # We do this only for lists and tuples. Calling `next()` on any
            # collection would create undesired side effects.
            if isinstance(expression_val, (list, tuple)) and expression_val:
                temp_locals[varname] = expression_val[0]

        # Get all completions.
        yield from self._get_expression_completions(
            document, complete_event, temp_locals
        )
        yield from self._get_item_lookup_completions(
            document, complete_event, temp_locals
        )
        yield from self._get_attribute_completions(
            document, complete_event, temp_locals
        )

    def _do_repr(self, obj: object) -> str:
        try:
            return str(repr(obj))
        except BaseException:
            raise ReprFailedError

    def _get_expression_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
        temp_locals: Dict[str, Any],
    ) -> Iterable[Completion]:
        """
        Complete the [ or . operator after an object.
        """
        match = self.expression_pattern.search(document.text_before_cursor)
        if match is not None:
            object_var = match.groups()[0]
            result = self._lookup(object_var, temp_locals)

            if isinstance(result, (list, tuple, dict)):
                yield Completion("[", 0)
            elif result:
                yield Completion(".", 0)

    def _get_item_lookup_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
        temp_locals: Dict[str, Any],
    ) -> Iterable[Completion]:
        """
        Complete dictionary keys.
        """
        match = self.item_lookup_pattern.search(document.text_before_cursor)
        if match is not None:
            object_var, key = match.groups()

            # Do lookup of `object_var` in the context.
            result = self._lookup(object_var, temp_locals)

            # If this object is a dictionary, complete the keys.
            if isinstance(result, dict):
                # Try to evaluate the key.
                key_obj = key
                for k in [key, key + '"', key + "'"]:
                    try:
                        key_obj = ast.literal_eval(k)
                    except (SyntaxError, ValueError):
                        continue
                    else:
                        break

                for k in result:
                    if str(k).startswith(key_obj):
                        try:
                            k_repr = self._do_repr(k)
                            yield Completion(
                                k_repr + "]",
                                -len(key),
                                display=f"[{k_repr}]",
                                display_meta=self._do_repr(result[k]),
                            )
                        except ReprFailedError:
                            pass

            # Complete list/tuple index keys.
            elif isinstance(result, (list, tuple)):
                if not key or key.isdigit():
                    for k in range(min(len(result), 1000)):
                        if str(k).startswith(key):
                            try:
                                k_repr = self._do_repr(k)
                                yield Completion(
                                    k_repr + "]",
                                    -len(key),
                                    display=f"[{k_repr}]",
                                    display_meta=self._do_repr(result[k]),
                                )
                            except ReprFailedError:
                                pass

    def _get_attribute_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
        temp_locals: Dict[str, Any],
    ) -> Iterable[Completion]:
        """
        Complete attribute names.
        """
        match = self.attribute_lookup_pattern.search(document.text_before_cursor)
        if match is not None:
            object_var, attr_name = match.groups()

            # Do lookup of `object_var` in the context.
            result = self._lookup(object_var, temp_locals)

            names = self._sort_attribute_names(dir(result))

            for name in names:
                if name.startswith(attr_name):
                    yield Completion(
                        name, -len(attr_name),
                    )

    def _sort_attribute_names(self, names: List[str]) -> List[str]:
        """
        Sort attribute names alphabetically, but move the double underscore and
        underscore names to the end.
        """

        def sort_key(name: str):
            if name.startswith("__"):
                return (2, name)  # Double underscore comes latest.
            if name.startswith("_"):
                return (1, name)  # Single underscore before that.
            return (0, name)  # Other names first.

        return sorted(names, key=sort_key)


class ReprFailedError(Exception):
    " Raised when the repr() call in `DictionaryCompleter` fails. "


try:
    import builtins

    _builtin_names = dir(builtins)
except ImportError:  # Python 2.
    _builtin_names = []


def _get_style_for_name(name: str) -> str:
    """
    Return completion style to use for this name.
    """
    if name in _builtin_names:
        return "class:completion.builtin"

    if keyword.iskeyword(name):
        return "class:completion.keyword"

    return ""
