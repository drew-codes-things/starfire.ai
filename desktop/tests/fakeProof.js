const crypto = require('node:crypto');

function isProofCall(call) {
  return call.path.startsWith('/link/proof?');
}

function proofAnswer(call, settings) {
  const nonce = new URL(call.path, 'http://127.0.0.1').searchParams.get('nonce');
  const fingerprint = crypto.createHash('sha256').update(settings.artyToken).digest('hex');
  const proof = crypto.createHmac('sha256', fingerprint).update(`arty-link-proof:${settings.artyPort}:${nonce}`).digest('hex');
  return { status: 200, body: { proof } };
}

module.exports = { isProofCall, proofAnswer };
