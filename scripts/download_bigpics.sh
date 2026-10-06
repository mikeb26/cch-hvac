#!/usr/bin/env bash
# Download and convert completed Screen 1 Big Picture telemetry exports.
set -Eeuo pipefail

readonly LOG_URL='https://www.noop.org/cmore/SD1/Log/'
readonly OUTPUT_DIR='./data'

tmp_dir=''
current_source=''
partial_output=''
phase='initializing'

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status=$?
    if [[ -n $partial_output ]]; then
        rm -f -- "$partial_output"
    fi
    if [[ -n $tmp_dir && -d $tmp_dir ]]; then
        rm -rf -- "$tmp_dir"
    fi
    exit "$status"
}

on_error() {
    local status=$?
    printf 'Error: %s failed' "$phase" >&2
    if [[ -n $current_source ]]; then
        printf ' for %s' "$current_source" >&2
    fi
    printf ' (line %s, exit status %s).\n' "$1" "$status" >&2
    exit "$status"
}

trap cleanup EXIT
trap 'on_error $LINENO' ERR

for command in wget perl sort iconv dos2unix gzip mktemp mv; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

mkdir -p -- "$OUTPUT_DIR"
tmp_dir=$(mktemp -d /tmp/bigpic-download.XXXXXX)
listing_file="$tmp_dir/listing.html"

printf 'Retrieving directory listing from %s\n' "$LOG_URL"
phase='retrieving the directory listing'
wget --quiet --output-document="$listing_file" "$LOG_URL"

# Each record is a YYMMDD date and its URL-encoded filename.  The Apache
# directory index uses URL-encoded href values, which are safe to append to
# LOG_URL without further transformation.
declare -A source_by_date=()
while IFS=$'\t' read -r date href; do
    if [[ -n ${source_by_date[$date]+present} ]]; then
        fail "Multiple Screen 1 Big Picture source files were listed for date $date"
    fi
    source_by_date[$date]=$href
done < <(
    perl -ne '
        while (/<a\s+href="([^"]+)"/gi) {
            $href = $1;
            if ($href =~ m{^Screen%201_Big%20Picture_([0-9]{6})\.txt(?:\.gz)?$}) {
                print "$1\t$href\n";
            }
        }
    ' "$listing_file"
)

((${#source_by_date[@]} > 0)) || fail 'No Screen 1 Big Picture files were found in the directory listing'

latest_date=''
for date in "${!source_by_date[@]}"; do
    if [[ -z $latest_date || $date > $latest_date ]]; then
        latest_date=$date
    fi
done
printf 'Skipping the most recent source file date: %s\n' "$latest_date"

mapfile -t dates < <(printf '%s\n' "${!source_by_date[@]}" | sort)
converted=0
for date in "${dates[@]}"; do
    href=${source_by_date[$date]}
    output_file="$OUTPUT_DIR/bigpic_$date.csv"

    [[ $date == "$latest_date" ]] && continue
    if [[ -e $output_file ]]; then
        printf 'Skipping existing %s\n' "$output_file"
        continue
    fi

    current_source=$href
    source_file="$tmp_dir/$href"
    decoded_file="$tmp_dir/Screen 1_Big Picture_$date.txt"
    partial_output="$OUTPUT_DIR/.bigpic_$date.csv.partial"

    printf 'Downloading %s\n' "$href"
    phase='downloading'
    wget --quiet --output-document="$source_file" "$LOG_URL$href"

    if [[ $href == *.gz ]]; then
        phase='decompressing'
        gzip --decompress --stdout -- "$source_file" > "$decoded_file"
    else
        decoded_file=$source_file
    fi

    phase='converting the source encoding'
    iconv --from-code=UTF-16LE --to-code=UTF-8 -- "$decoded_file" > "$partial_output"
    phase='normalizing line endings'
    dos2unix --quiet "$partial_output"
    phase='publishing the converted file'
    mv -- "$partial_output" "$output_file"
    partial_output=''
    printf 'Created %s\n' "$output_file"
    current_source=''
    ((converted += 1))
done

printf 'Completed: converted %d file(s).\n' "$converted"
