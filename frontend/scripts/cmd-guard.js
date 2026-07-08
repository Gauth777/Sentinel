#!/usr/bin/env node

// Modes: --cmd <bin> --args <...> | --preinstall | --list-commands
// Shim exit codes: 0 allow, 1 block, 2 rewrite (replacement argv on stdout).

const { runArgs, runPreinstall, runListCommands } = require("./cmd-guard/modes");

const argv = process.argv.slice(2);
if (argv[0] === "--preinstall") runPreinstall();
else if (argv[0] === "--list-commands") runListCommands();
else {
  let cmd = "";
  let args = [];
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === "--cmd") cmd = argv[++i];
    else if (argv[i] === "--args") {
      args = argv.slice(i + 1);
      break;
    }
  }
  runArgs(cmd, args);
}
