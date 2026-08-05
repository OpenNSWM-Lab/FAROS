import py_compile, os
ok = fail = 0
for root, dirs, files in os.walk('.'):
  for f in files:
    if f.endswith('.py') and '_faros_' not in f:
      fp = os.path.join(root, f)
      try:
        py_compile.compile(fp, doraise=True)
        ok += 1
        print('OK', fp)
      except py_compile.PyCompileError as e:
        fail += 1
        print('FAIL', fp, str(e))
print(f'\nSyntax check done: {ok} OK, {fail} FAIL')
