from kb.linkers import ts


def tree_of(tmp_path, code, name="a.tsx"):
    p = tmp_path / name
    p.write_text(code)
    return ts.parse(p)


def test_tagged_template_text_line_and_var(tmp_path):
    t = tree_of(tmp_path, "import x from 'y';\nexport const addTaskQuery = gql`\n  mutation AddTask { AddTask { eid } }\n`;\n")
    [hit] = ts.tagged_templates(t, "gql")
    assert "mutation AddTask" in hit.text
    assert hit.line == 2
    assert hit.var_name == "addTaskQuery"


def test_calls_with_member_first_arg(tmp_path):
    t = tree_of(tmp_path, "TaskQueue.add(TaskTopics.TASK_REPEAT, {});\nJobsQueue.process('SIGN_UP', h);\nQ.process(topic, h);\n", "q.ts")
    adds = ts.calls(t, "add")
    assert [(c.receiver, c.first_arg, c.first_arg_kind, c.line) for c in adds] == [("TaskQueue", "TaskTopics.TASK_REPEAT", "member", 1)]
    procs = ts.calls(t, "process")
    assert [(c.first_arg, c.first_arg_kind) for c in procs] == [("SIGN_UP", "string"), ("topic", "identifier")]


def test_object_literals_with_key(tmp_path):
    t = tree_of(tmp_path, "const AddTask = {\n  name: 'AddTask',\n  resolve: async () => {},\n};\n", "r.ts")
    [o] = ts.object_literals_with(t, "resolve")
    assert o.props["name"] == "'AddTask'"
    assert o.line == 1 and o.var_name == "AddTask"


def test_jsx_elements_attrs(tmp_path):
    t = tree_of(tmp_path, "const A = () => (<Switch>\n<Route path={TASKS} component={Page} />\n<Route path='/x' element={<Other />} />\n</Switch>);\n")
    hits = ts.jsx_elements(t, "Route")
    assert hits[0].attrs == {"path": "TASKS", "component": "Page"}
    assert hits[0].line == 2
    assert hits[1].attrs["path"] == "'/x'"
    assert hits[1].attrs["element"] == "<Other />"


def test_string_constants(tmp_path):
    t = tree_of(tmp_path, "export const TASKS_CREATE_URL = '/tasks/create-task';\nconst N = 3;\n", "r.ts")
    assert ts.string_constants(t) == {"TASKS_CREATE_URL": "/tasks/create-task"}


def test_syntax_error_still_parses_and_unreadable_returns_none(tmp_path):
    t = tree_of(tmp_path, "const x = gql`query A { a }`;\nfunction (((\n", "bad.ts")
    assert t is not None and len(ts.tagged_templates(t, "gql")) == 1
    assert ts.parse(tmp_path / "missing.ts") is None


def test_iter_source_files_skips_node_modules_and_ignored(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("")
    (tmp_path / "src" / "b.svg").write_text("")
    (tmp_path / "src" / "gen.ts").write_text("")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.js").write_text("")
    files = [p.relative_to(tmp_path).as_posix() for p in ts.iter_source_files(tmp_path, ["**/gen.ts"])]
    assert files == ["src/a.ts"]
