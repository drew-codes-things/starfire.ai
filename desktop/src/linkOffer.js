const fs = require('node:fs');
const path = require('node:path');

const ARTY_FOLDER = 'Arty';
const OFFER_NAME = '.arty_link_offer';
const MAX_OFFER_BYTES = 64;
const OFFER_FORMAT = /^[0-9a-f]{32}$/;
const GROUP_AND_OTHER_BITS = 0o077;
const OPEN_WITHOUT_FOLLOWING = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0);

function offerFile(appDataFolder) {
  return typeof appDataFolder === 'string' && appDataFolder !== '' ? path.join(appDataFolder, ARTY_FOLDER, OFFER_NAME) : '';
}

function currentUserId() {
  return typeof process.getuid === 'function' ? process.getuid() : null;
}

function isPrivateOfferFile(stats, userId) {
  if (!stats.isFile() || stats.isSymbolicLink() || stats.size > MAX_OFFER_BYTES) return false;
  if (userId === null) return true;
  return stats.uid === userId && (stats.mode & GROUP_AND_OTHER_BITS) === 0;
}

function isSameFile(first, second) {
  return first.dev === second.dev && first.ino === second.ino;
}

function readOpenedOffer(files, descriptor, listed, userId) {
  const opened = files.fstatSync(descriptor);
  if (!isPrivateOfferFile(opened, userId) || !isSameFile(opened, listed)) return null;
  const buffer = Buffer.alloc(MAX_OFFER_BYTES + 1);
  const length = files.readSync(descriptor, buffer, 0, buffer.length, 0);
  const text = buffer.subarray(0, length).toString('latin1');
  return OFFER_FORMAT.test(text) ? text : null;
}

function readOffer(file, { files = fs, userId = currentUserId() } = {}) {
  if (typeof file !== 'string' || file === '') return null;
  let listed;
  try {
    listed = files.lstatSync(file);
  } catch (error) {
    return null;
  }
  if (!isPrivateOfferFile(listed, userId)) return null;
  let descriptor;
  try {
    descriptor = files.openSync(file, OPEN_WITHOUT_FOLLOWING);
    return readOpenedOffer(files, descriptor, listed, userId);
  } catch (error) {
    return null;
  } finally {
    if (descriptor !== undefined) files.closeSync(descriptor);
  }
}

module.exports = { offerFile, readOffer };
