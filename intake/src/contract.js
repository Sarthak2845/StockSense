const path = require('path');
const Ajv2020 = require('ajv/dist/2020');
const addFormats = require('ajv-formats');
const { PayloadError } = require('./errors');

// Single source of truth for the event shape, shared with the processor.
const SCHEMA_PATH = path.join(__dirname, '..', '..', 'docs', 'contracts', 'internal-event.schema.json');
const schema = require(SCHEMA_PATH);

const ajv = new Ajv2020({ allErrors: true });
addFormats(ajv);
const validate = ajv.compile(schema);

function assertValidEvent(evt) {
  if (!validate(evt)) {
    throw new PayloadError(`event does not match contract: ${ajv.errorsText(validate.errors)}`);
  }
  return evt;
}

module.exports = { assertValidEvent };
