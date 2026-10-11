#!/usr/bin/env bash
# Download and convert completed PLC telemetry exports selected interactively.
set -Eeuo pipefail

readonly LOG_URL='https://www.noop.org/cmore/SD1/Log/'
readonly OUTPUT_BASE_DIR='./data'

tmp_dir=''
current_source=''
partial_output=''
phase='initializing'
log_type=''
source_pattern=''
output_subdir=''
output_prefix=''

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

choose_log_type() {
    printf 'Select the log type to download:\n'
    printf '  1) Screen 1: Big Picture\n'
    printf '  2) Screen 2: Well Detail\n'
    printf '  3) Screen 3: HP Detail\n'
    printf '  4) Screen 4: Error Log\n'
    printf '  5) Screen 5: P1-P2 Modbus\n'
    printf '  6) Screen 6: Debug Screen 6\n'

    local selection
    while true; do
        if ! read -r -p 'Enter a selection (1-6): ' selection; then
            fail 'No log type was selected'
        fi

        case $selection in
            1)
                log_type='Screen 1 Big Picture'
                source_pattern='Screen%201_Big%20Picture'
                output_subdir='bigpic'
                output_prefix='bigpic'
                return
                ;;
            2)
                log_type='Screen 2 Well Detail'
                source_pattern='Screen%202_Well%20Detail'
                output_subdir='well-detail'
                output_prefix='well-detail'
                return
                ;;
            3)
                log_type='Screen 3 HP Detail'
                source_pattern='Screen%203_HP%20Detail'
                output_subdir='hp-detail'
                output_prefix='hp-detail'
                return
                ;;
            4)
                log_type='Screen 4 Error Log'
                source_pattern='Screen%204_Error%20Log'
                output_subdir='error-log'
                output_prefix='error-log'
                return
                ;;
            5)
                log_type='Screen 5 P1-P2 Modbus'
                source_pattern='Screen%205_P1-P2Modbus'
                output_subdir='p1-p2-modbus'
                output_prefix='p1-p2-modbus'
                return
                ;;
            6)
                log_type='Screen 6 Debug Screen 6'
                source_pattern='Screen%206_Debug%20Scrn%206'
                output_subdir='debug-screen-6'
                output_prefix='debug-screen-6'
                return
                ;;
            *)
                printf 'Invalid selection %q. Enter a number from 1 through 6.\n' "$selection" >&2
                ;;
        esac
    done
}

trap cleanup EXIT
trap 'on_error $LINENO' ERR

for command in wget perl sort iconv dos2unix gzip mktemp mv; do
    command -v "$command" >/dev/null 2>&1 || fail "Required command not found: $command"
done

choose_log_type
output_dir="$OUTPUT_BASE_DIR/$output_subdir"
mkdir -p -- "$output_dir"
tmp_dir=$(mktemp -d /tmp/plc-log-download.XXXXXX)
listing_file="$tmp_dir/listing.html"

printf 'Retrieving the %s directory listing from %s\n' "$log_type" "$LOG_URL"
phase='retrieving the directory listing'
wget --quiet --output-document="$listing_file" "$LOG_URL"

# Each record is a YYMMDD date and its URL-encoded filename for the selected
# fixed log type. The Apache directory index uses URL-encoded href values,
# which are safe to append to LOG_URL without further transformation.
declare -A source_by_date=()
while IFS=$'\t' read -r date href; do
    if [[ -n ${source_by_date[$date]+present} ]]; then
        fail "Multiple $log_type source files were listed for date $date"
    fi
    source_by_date[$date]=$href
done < <(
    SOURCE_PATTERN="$source_pattern" perl -ne '
        BEGIN { $source_pattern = $ENV{SOURCE_PATTERN}; }
        while (/<a\s+href="([^"]+)"/gi) {
            $href = $1;
            if ($href =~ m{^$source_pattern\_([0-9]{6})\.txt(?:\.gz)?$}) {
                print "$1\t$href\n";
            }
        }
    ' "$listing_file"
)

((${#source_by_date[@]} > 0)) || fail "No $log_type files were found in the directory listing"

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
    output_file="$output_dir/${output_prefix}_${date}.csv"

    [[ $date == "$latest_date" ]] && continue
    if [[ -e $output_file ]]; then
        printf 'Skipping existing %s\n' "$output_file"
        continue
    fi

    current_source=$href
    source_file="$tmp_dir/$href"
    decoded_file="$tmp_dir/$date.txt"
    partial_output="$output_dir/.${output_prefix}_${date}.csv.partial"

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
