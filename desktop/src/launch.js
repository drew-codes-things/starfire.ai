const QUICK_ASK_FLAG = '--quick-ask';
const HIDDEN_FLAG = '--hidden';

function parseLaunchArgs(argv) {
  const args = Array.isArray(argv) ? argv : [];
  return { quickAsk: args.includes(QUICK_ASK_FLAG), hidden: args.includes(HIDDEN_FLAG) };
}

module.exports = { parseLaunchArgs, QUICK_ASK_FLAG, HIDDEN_FLAG };
