const { createHash, createHmac, randomBytes } = require('node:crypto');

const PRIME = BigInt(`0x${[
  'FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD',
  'EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED',
  'EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F',
  '83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B',
  'E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510',
  '15728E5A8AACAA68FFFFFFFFFFFFFFFF',
].join('')}`);
const GENERATOR = 2n;
const ORDER = (PRIME - 1n) / 2n;
const ELEMENT_BYTES = 256;
const SHARE_HEX_CHARS = ELEMENT_BYTES * 2;
const SECRET_EXTRA_BYTES = 8;
const CONTEXT = 'arty-link-pair-v1';
const HASH_BLOCKS_PER_ELEMENT = 5;
const SHARE_FORMAT = /^[0-9a-f]+$/;
const APP = 'app';
const ARTY = 'arty';

function modPow(base, exponent, modulus) {
  let result = 1n;
  let square = base % modulus;
  let remaining = exponent;
  while (remaining > 0n) {
    if (remaining & 1n) result = (result * square) % modulus;
    square = (square * square) % modulus;
    remaining >>= 1n;
  }
  return result;
}

function bytesToBigInt(bytes) {
  return BigInt(`0x${Buffer.from(bytes).toString('hex') || '0'}`);
}

function elementBytes(element) {
  return Buffer.from(element.toString(16).padStart(SHARE_HEX_CHARS, '0'), 'hex');
}

function hashedElement(label) {
  const blocks = [];
  for (let index = 0; index < HASH_BLOCKS_PER_ELEMENT; index += 1) {
    blocks.push(createHash('sha512').update(Buffer.concat([Buffer.from(`${CONTEXT}|${label}`), Buffer.from([index])])).digest());
  }
  return modPow(bytesToBigInt(Buffer.concat(blocks)) % PRIME, 2n, PRIME);
}

const BLINDS = { [APP]: hashedElement('M'), [ARTY]: hashedElement('N') };

function otherRole(role) {
  return role === APP ? ARTY : APP;
}

function codeScalar(code) {
  return bytesToBigInt(createHash('sha512').update(`${CONTEXT}|code|${code}`, 'utf8').digest()) % ORDER;
}

function newSecret() {
  return (bytesToBigInt(randomBytes(ELEMENT_BYTES + SECRET_EXTRA_BYTES)) % (ORDER - 1n)) + 1n;
}

function shareHex(secret, code, role) {
  const element = (modPow(GENERATOR, secret, PRIME) * modPow(BLINDS[role], codeScalar(code), PRIME)) % PRIME;
  return elementBytes(element).toString('hex');
}

function elementFromHex(text) {
  if (typeof text !== 'string' || text.length !== SHARE_HEX_CHARS || !SHARE_FORMAT.test(text)) {
    throw new Error('A pairing share is 512 lowercase hex characters.');
  }
  const element = BigInt(`0x${text}`);
  const isInGroup = element > 1n && element < PRIME - 1n && modPow(element, ORDER, PRIME) === 1n;
  if (!isInGroup) throw new Error('That pairing share is not a value this pairing can use.');
  return element;
}

function sharedKey(secret, code, role, appShare, artyShare) {
  const theirShare = elementFromHex(role === APP ? artyShare : appShare);
  elementFromHex(role === APP ? appShare : artyShare);
  const scalar = codeScalar(code);
  const unblinded = (theirShare * modPow(BLINDS[otherRole(role)], ORDER - scalar, PRIME)) % PRIME;
  const shared = modPow(unblinded, secret, PRIME);
  const transcript = Buffer.concat([
    Buffer.from(CONTEXT), Buffer.from(appShare, 'hex'), Buffer.from(artyShare, 'hex'), elementBytes(shared), elementBytes(scalar),
  ]);
  return createHash('sha256').update(transcript).digest();
}

function proof(key, role) {
  return createHmac('sha256', key).update(`${CONTEXT}|proof|${role}`).digest('hex');
}

function linkToken(key) {
  return createHmac('sha256', key).update(`${CONTEXT}|token`).digest('base64url');
}

module.exports = { APP, ARTY, PRIME, ORDER, SHARE_HEX_CHARS, newSecret, shareHex, sharedKey, proof, linkToken };
