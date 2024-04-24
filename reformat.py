from pathlib import Path
from typing import Optional, Sequence
import urllib.parse
import logging
import re
import asyncio
import requests
import httpx

BASE_FOLDER = Path(__file__).parent.resolve()
BASE_APP_ADS_ADS_FILE = Path(BASE_FOLDER).joinpath("app-ads.txt")
TAG_REGISTRY_API_TEMPLATE = "https://tag-members-prod.herokuapp.com/registry/lookup?q={certification_authority_id}"

logging.basicConfig(encoding='utf-8', level=logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)
logger = logging.getLogger()


# https://iabtechlab.com/wp-content/uploads/2022/04/Ads.txt-1.1.pdf
class DataRecord:
    # https://validators.readthedocs.io/en/latest/_modules/validators/domain.html#domain
    DOMAIN_PATTERN = re.compile(
                        r'^(([a-zA-Z]{1})|([a-zA-Z]{1}[a-zA-Z]{1})|'
                        r'([a-zA-Z]{1}[0-9]{1})|([0-9]{1}[a-zA-Z]{1})|'
                        r'([a-zA-Z0-9][-_.a-zA-Z0-9]{0,61}[a-zA-Z0-9]))\.'
                        r'([a-zA-Z]{2,13}|[a-zA-Z0-9-]{2,30}.[a-zA-Z]{2,3})$'
                     )
    ALLOWED_RELATIONSHIPS = ('DIRECT', 'RESELLER')

    domain: str
    publisher_id: str
    relationship: str
    certification_authority_id: str
    extension_fields: str

    @classmethod
    def parse(cls, line: str) -> Optional['DataRecord']:
        record = cls()
        parts = [part.strip() for part in line.split(';')]
        if len(parts) > 1:
            logger.info(f"extension fields found in line {line}")
            record.extension_fields = ';'.join(parts[1:])
        else:
            record.extension_fields = None
        line = parts[0]
        parts = [part.strip() for part in line.split(',')]
        if not all(parts):
            logger.warning(f"line {line} has empty fields")
            return None
        if len(parts) < 3:
            logger.warning(f"line {line} is missing some of the required fields({len(parts)} out of 3 required)")
            return None
        if len(parts) > 4:
            logger.warning(f"line {line} has more fields than expected({len(parts)} out of 4 maximum fields)")
            return None
        record.domain = cls.validated_domain(parts[0])
        if record.domain is None:
            return None
        record.publisher_id = parts[1]
        record.relationship = cls.validated_relationship(parts[2])
        if record.relationship is None:
            return None
        record.certification_authority_id = parts[3] if len(parts) >= 4 else None

        return record

    @classmethod
    def validated_domain(cls, domain: str) -> Optional[str]:
        normalized = domain.lower()
        if cls.DOMAIN_PATTERN.match(normalized) is None:
            logger.warning(f"invalid domain name {domain} found in line")
            return None
        return normalized

    @classmethod
    def validated_relationship(cls, relationship: str) -> Optional[str]:
        normalized = relationship.upper()
        if normalized not in cls.ALLOWED_RELATIONSHIPS:
            logger.warning(f"unsupported relationship {relationship} found in line(allowed relationships: {cls.ALLOWED_RELATIONSHIPS})")
            return None
        return normalized

    @property
    def line(self) -> str:
        parts = [self.domain, self.publisher_id, self.relationship]
        if self.certification_authority_id is not None:
            parts.append(self.certification_authority_id)
        result = ', '.join(parts)
        if self.extension_fields is not None:
            result = f"{result}; {self.extension_fields}"
        return result

    def __eq__(self, other: 'DataRecord'):
        if not isinstance(other, DataRecord):
            return False
        return self.line == other.line

    def __hash__(self):
        return hash(self.line)


def validate_certificate_authority_ids(records: Sequence[DataRecord]) -> bool:
    MAX_SIMULTANEOUS_REQUESTS = 20
    MAX_TIMEOUT_RETRIES = 3

    async def check_all_ca_ids(unique_ca_ids: Sequence[str]) -> bool:
        remaining_requests = [(ca_id, 0) for ca_id in unique_ca_ids]
        request_tasks = []
        validation_errors = False
        while len(remaining_requests) > 0 or len(request_tasks) > 0:
            while len(remaining_requests) > 0 and len(request_tasks) < MAX_SIMULTANEOUS_REQUESTS:
                ca_id, try_count = remaining_requests.pop()
                task = asyncio.create_task(check_ca_id(ca_id=ca_id, try_count=try_count))
                request_tasks.append(task)
            done, pending = await asyncio.wait(request_tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                result = task.result()
                # handle timeout error
                if len(result) == 2:
                    ca_id, try_count = result
                    remaining_requests.append((ca_id, try_count + 1))
                else:
                    ca_id, verified, response_body = result
                    if not verified:
                        logger.warning(f"certification authority with id {ca_id} could not be verified and shall be removed")
                        logger.debug(f"response body: {response_body}")
                        validation_errors = True
                    else:
                        logger.debug(f"certification authority with id {ca_id} verified")
                request_tasks.remove(task)
        return validation_errors

    async def check_ca_id(ca_id: str, try_count: int):
        url = TAG_REGISTRY_API_TEMPLATE.format(certification_authority_id=urllib.parse.quote(ca_id))
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.request(method='get', url=url)
            except httpx.TimeoutException:
                if try_count >= MAX_TIMEOUT_RETRIES:
                    logger.error(f"max timeout retries reached {MAX_TIMEOUT_RETRIES} while requesting {url}")
                    raise
                logger.debug(f"retrying request {url} due to timeout")
                return (ca_id, try_count)
        return (ca_id, re.search(r'<strong>\s+active\.\s+</strong>', response.text) is not None, response.text)

    records_with_ca_id = filter(lambda x: x.certification_authority_id is not None, records)
    unique_ca_ids = set(map(lambda x: x.certification_authority_id, records_with_ca_id))
    return asyncio.run(check_all_ca_ids(unique_ca_ids=unique_ca_ids))

def sort_and_format_file(file_path: Path) -> bool:
    validation_errors = False
    original_lines = []
    records = set()
    lines = []
    logger.info(f"Formatting {file_path}:")

    with open(str(file_path), "r") as file:
        for line in file:
            original_lines.append(line.strip('\n'))
            record = DataRecord.parse(line)
            if record is not None:
                lines.append(record.line)
                records.add(record)
            else:
                line = line.strip('\n')
                logger.warning(f"manually fix line {line} and reformat again")
                lines.append(line)
                validation_errors = True

    unique_lines = list(set(lines))
    sorted_lines = sorted(unique_lines)

    with open(str(file_path), "w") as file:
        for line in sorted_lines:
            file.write(f"{line}\n")

    if sorted_lines != original_lines:
        if len(sorted_lines) == len(original_lines):
            logger.info("Some lines has been sorted")

        set_original = set(original_lines)
        set_sorted = set(sorted_lines)
        removed_lines = set_original.difference(set_sorted)
        new_lines = set_sorted.difference(set_original)

        if removed_lines:
            num_duplicated_lines = len(removed_lines) - len(new_lines)
            if num_duplicated_lines > 0:
                logger.info(f"{num_duplicated_lines} lines has been deleted because were duplicated")

            logger.info(f"{len(removed_lines) - num_duplicated_lines} lines has been formatted")
    else:
        logger.info(f"No changes done in the file")

    logger.info("Validating certification authorities ids")
    validation_errors |= validate_certificate_authority_ids(records)
    return validation_errors

def main():
    validation_errors = False
    validation_errors |= sort_and_format_file(file_path=BASE_APP_ADS_ADS_FILE)
    if validation_errors:
        logger.error("There are one or more validation errors that require manual fixing")
        # exit(1)

if __name__ == '__main__':
    main()
