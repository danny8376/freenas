#!/bin/sh

# vim: noexpandtab ts=8 sw=4 softtabstop=4

# Setup a semi-sane environment
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin:/rescue
export PATH
HOME=/root
export HOME
TERM=${TERM:-xterm}
export TERM

PROJECT="FreeNAS"
VERSION="12-STABLE"

if [ -e "/var/db/trueos-manifest.json" ] ; then
  _tmp=`jq -r '."os_name"' "/var/db/trueos-manifest.json"`
  if [ -n "${_tmp}" -a "${_tmp}" != "null" ] ; then
    PROJECT="${_tmp}"
  fi
  #FIXME: let's avoid duplication please
  _tmp=`jq -r '."os_version"' "/var/db/trueos-manifest.json"`
  if [ -n "${_tmp}" -a "${_tmp}" != "null" ] ; then
    VERSION="${_tmp}"
  fi
fi

# Constants for base 10 and base 2 units
: ${kB:=$((1000))}      ${kiB:=$((1024))};       readonly kB kiB
: ${MB:=$((1000 * kB))} ${MiB:=$((1024 * kiB))}; readonly MB MiB
: ${GB:=$((1000 * MB))} ${GiB:=$((1024 * MiB))}; readonly GB GiB
: ${TB:=$((1000 * GB))} ${TiB:=$((1024 * GiB))}; readonly TB TiB

is_truenas()
{

    test "${PROJECT}" = "TrueNAS"
    return $?
}

# Constant media size threshold for allowing swap partitions.
: ${MIN_SWAPSAFE_MEDIASIZE:=$((60 * GB))}; readonly MIN_SWAPSAFE_MEDIASIZE

# Check if it is safe to create swap partitions on the given disks.
#
# The result can be forced by setting SWAP_IS_SAFE in the environment to either
# "YES" or "NO".
#
# Sets SWAP_IS_SAFE to "YES" if
#   we are on TrueNAS
#   *or*
#   every disk in $@ is >= ${MIN_SWAPSAFE_MEDIASIZE} and none is USB and user says ok
# Otherwise sets SWAP_IS_SAFE to "NO".
#
# Use `is_swap_safe` to check the value of ${SWAP_IS_SAFE}.
check_is_swap_safe()
{
    # We assume swap is safe on TrueNAS,
    # and we try to use the existing value for ${SWAP_IS_SAFE} if already set.
    if ! is_truenas && [ -z "${SWAP_IS_SAFE}" ] ; then
	local _disk
	# Check every disk in $@, aborting if an unsafe disk is found.
	for _disk ; do
	    if [ $(diskinfo "${_disk}" | cut -f 3) -lt ${MIN_SWAPSAFE_MEDIASIZE} ] ||
		camcontrol negotiate "${_disk}" -v | grep -qF 'umass-sim' ; then
		SWAP_IS_SAFE="NO"
		break
	    fi
	done
    fi
    # Make sure we have a valid value for ${SWAP_IS_SAFE}.
    # If unset, we are either on TrueNAS or didn't find an unsafe disk.
    case "${SWAP_IS_SAFE:="YES"}" in
	# Accept YES or NO (case-insensitive).
	[Yy][Ee][Ss])
	    # Confirm swap setup with FreeNAS users.
	    if ! is_truenas &&
		! dialog --clear --title "${PROJECT}" \
		    --yes-label "Create swap" --no-label "No swap" --yesno  \
		    "Create 16GB swap partition on boot devices?" \
		    7 74 ; then
		SWAP_IS_SAFE="NO"
	    fi
	    ;;
	[Nn][Oo]) ;;
	# Reject other values.
	*)  echo "Ignoring invalid value for SWAP_IS_SAFE: ${SWAP_IS_SAFE}"
	    unset SWAP_IS_SAFE
	    check_is_swap_safe "$@"
	    ;;
    esac
    export SWAP_IS_SAFE
}

# A specialized checkyesno for SWAP_IS_SAFE.
# Returns 0 if it is ok to set up swap on the chosen disks, otherwise 1.
# `check_is_swap_safe` must be called once before calling `is_swap_safe`.
is_swap_safe()
{
    case "${SWAP_IS_SAFE:?}" in
	[Yy][Ee][Ss]) true;;
	*) false;;
    esac
}

do_sata_dom()
{

    if ! is_truenas ; then
	return 1
    fi
    install_sata_dom_prompt
    return $?
}

get_product_path()
{
    echo /cdrom /.mount
}

get_image_name()
{
    find $(get_product_path) -name "${PROJECT}-${VERSION}.img.xz" -type f
}

# The old pre-install checks did several things
# 1:  Don't allow going from FreeNAS to TrueNAS or vice versa
# 2:  Don't allow downgrading.  (Not sure we can do that now.)
# 3:  Check memory size and cpu speed.
# This does memory size only for now.
pre_install_check()
{
    # We need at least 8 GB of RAM
    # minus 1 GB to allow for reserved memory
    local minmem=$((7 * GiB))
    local memsize=$(sysctl -n hw.physmem)

    if [ ${memsize} -lt ${minmem} ]; then
	dialog --clear --title "${PROJECT}" --defaultno \
	--yesno "This computer has less than the recommended 8 GB of RAM.\n\nOperation without enough RAM is not recommended.  Continue anyway?" 7 74 || return 1
    fi
    return 0
}

build_config_old()
{
    # build_config ${_disk} ${_image} ${_config_file}

    local _disk=$1
    local _image=$2
    local _config_file=$3

    cat << EOF > "${_config_file}"
# Added to stop pc-sysinstall from complaining
installMode=fresh
installInteractive=no
installType=FreeBSD
installMedium=dvd
packageType=tar

disk0=${_disk}
partition=image
image=${_image}
bootManager=bsd
commitDiskPart
EOF
}

build_config()
{
    # build_config ${_disk} ${_image} ${_config_file}

    local _disk=$1
    local _image=$2
    local _config_file=$3

    cat << EOF > "${_config_file}"
# Added to stop pc-sysinstall from complaining
installMode=fresh
installInteractive=no
installType=FreeBSD
installMedium=dvd
packageType=tar

disk0=${_disk}
partscheme=GPT
partition=all
bootManager=bsd
commitDiskPart
EOF
}

wait_keypress()
{
    local _tmp
    read -p "Press ENTER to continue." _tmp
}

sort_disklist()
{

    sed 's/\([^0-9]*\)/\1 /' | sort +0 -1 +1n | tr -d ' '
}

# return 0 if no raid devices, or !0 if there are some.
get_raid_present()
{
	local _cnt
	local _dummy

	if [ ! -d "/dev/raid" ] ; then
		return 0;
	fi

	_cnt=0
	ls /dev/raid/ > /tmp/raidfiles
	while read _dummy ; do _cnt=$(($_cnt + 1));done < /tmp/raidfiles
	return $_cnt
}

get_physical_disks_list()
{
    local _boot=$(glabel status | awk '/iso9660\/(FREE|TRUE)NAS/ { print $3 }')
    local _disk

    for _disk in $(sysctl -n kern.disks)
    do
	if [ "${_disk}" = "${_boot}" ]; then
	    continue
	fi
	VAL="${VAL} ${_disk}"
    done

    get_raid_present
    if [ $? -ne 0 ] ; then
	VAL="$VAL `cd /dev ; ls -d raid/* | grep -v '[0-9][a-z]'`"
    fi

    VAL=`echo $VAL | tr ' ' '\n'| grep -v '^cd' | sort_disklist`
    export VAL
}

get_media_description()
{
    local _media
    local _description
    local _cap

    _media=$1
    if [ -n "${_media}" ]; then
	_description=`geom disk list ${_media} 2>/dev/null \
	    | sed -ne 's/^   descr: *//p'`
	if [ -z "${_description}" ]; then
	    _description="Unknown Device"
	fi
	_cap=`diskinfo ${_media} | awk \
	    -v TiB=${TiB}.0 \
	    -v GiB=${GiB}.0 \
	    -v MiB=${MiB}.0 \
	'{
	    capacity = int($3);
	    if (capacity >= TiB) {
	        printf("%.1f TiB", capacity / TiB);
	    } else if (capacity >= GiB) {
	        printf("%.1f GiB", capacity / GiB);
	    } else if (capacity >= MiB) {
	        printf("%.1f MiB", capacity / MiB);
	    } else {
	        printf("%d Bytes", capacity);
	    }
	}'`
	echo "${_description} -- ${_cap}"
    fi
}

disk_is_mounted()
{
    local _dev

    for _dev
    do
	if mount -v | grep -qE "^/dev/${_dev}[sp][0-9]+"
	then
	    return 0
	fi
    done
    return 1
}


new_install_verify()
{
    local _type="$1"
    shift
    local _upgradetype="$1"
    shift

    local _disks="$*"
    local _tmpfile="/tmp/msg"
    cat << EOD > "${_tmpfile}"
WARNING:
EOD

    if [ "$_type" = "upgrade" -a "$_upgradetype" = "inplace" ] ; then
      echo "- This will install into existing zpool on ${_disks}." >> ${_tmpfile}
    else
      echo "- This will erase ALL partitions and data on ${_disks}." >> ${_tmpfile}
    fi

    cat << EOD >> "${_tmpfile}"
- You can't use ${_disks} for sharing data.

NOTE:
- Installing on flash media is preferred to installing on a
  hard drive.

Proceed with the ${_type}?
EOD
    _msg=`cat "${_tmpfile}"`
    rm -f "${_tmpfile}"
    dialog --clear --title "${PROJECT} ${_type}" --yesno "${_msg}" 13 74
    [ $? -eq 0 ] || exit 1
}

ask_upgrade()
{
    local _disk="$1"
    local _tmpfile="/tmp/msg"
    cat << EOD > "${_tmpfile}"
Upgrading the installation will preserve your existing configuration.

Do you wish to perform an upgrade or a fresh installation on ${_disk}?
EOD
    _msg=`cat "${_tmpfile}"`
    rm -f "${_tmpfile}"
    dialog --title "Upgrade this ${PROJECT} installation" --no-label "Fresh Install" --yes-label "Upgrade Install" --yesno "${_msg}" 8 74
    return $?
}

ask_upgrade_inplace()
{
    local _tmpfile="/tmp/msg"
    cat << EOD > "${_tmpfile}"
User configuration settings and storage volumes are preserved and not affected by this step.\n\n
The boot device can be formatted to remove old versions, or the upgrade can be installed in a new boot environment without affecting any existing versions.
EOD
    _msg=`cat "${_tmpfile}"`
    rm -f "${_tmpfile}"
    dialog --trim --title "Update Method Selection" --yes-label "Install in new boot environment" --no-label "Format the boot device" --yesno "${_msg}" 0 0
    return $?
}

ask_boot_method()
{
    # If we are not on efi, set BIOS as the default selected option
    dlgflags=""
    if [ "$BOOTMODE" != "UEFI" ] ; then
      dlgflags="--defaultno"
    fi

    local _tmpfile="/tmp/msg"
    cat << EOD > "${_tmpfile}"
${PROJECT} can be booted in either BIOS or UEFI mode.

BIOS mode is recommended for legacy and enterprise hardware,
whereas UEFI may be required for newer consumer motherboards.
EOD
    _msg=`cat "${_tmpfile}"`
    rm -f "${_tmpfile}"
    dialog ${dlgflags} --title "${PROJECT} Boot Mode" --no-label "Boot via BIOS" --yes-label "Boot via UEFI" --yesno "${_msg}" 8 74
    return $?
}

install_loader()
{
    local _disk _disks
    local _mnt

    _mnt="$1"
    shift
    _disks="$*"


    # When doing inplace upgrades, its entirely possible we've
    # booted in the wrong mode (I.E. bios/efi)
    # Default to re-stamping what was already used on the current install
    _boottype="$BOOTMODE"
    if [ "${_upgrade_type}" = "inplace" ] ; then
      if gpart show ${_disks} | grep -qF 'efi' ; then
         _boottype="UEFI"
      else
         _boottype="BIOS"
      fi
    fi

    for _disk in $_disks
    do
	if [ "$_boottype" = "UEFI" ] ; then
	    echo "Stamping EFI loader on: ${_disk}"
	    mkdir -p /tmp/efi
	    mount -t msdosfs /dev/${_disk}p1 /tmp/efi
	    # Copy the .efi file
	    mkdir -p /tmp/efi/efi/boot
	    cp ${_mnt}/boot/boot1.efi /tmp/efi/efi/boot/BOOTx64.efi
	    umount /tmp/efi
	else
	    echo "Stamping GPT loader on: ${_disk}"
	    gpart modify -i 1 -t freebsd-boot ${_disk}
	    chroot ${_mnt} gpart bootcode -b /boot/pmbr -p /boot/gptzfsboot -i 1 /dev/${_disk}
	fi
    done

    return 0
}

save_serial_settings()
{
    _mnt="$1"

    # If the installer was booted with serial mode enabled, we should
    # save these values to the installed system
    USESERIAL=$((`sysctl -n debug.boothowto` & 0x1000))
    if [ "$USESERIAL" -eq 0 ] ; then return 0; fi

    # BIOS has vidconsole, UEFI has efi.
    if [ "$BOOTMODE" = "UEFI" ] ; then
       videoconsole="efi"
    else
       videoconsole="vidconsole"
    fi

    # Enable serial/internal for BSD loader
    echo 'boot_multicons="YES"' >> ${_mnt}/boot/loader.conf
    echo 'boot_serial="YES"' >> ${_mnt}/boot/loader.conf
    echo "console=\"comconsole,${videoconsole}\"" >> ${_mnt}/boot/loader.conf

    chroot ${_mnt} /usr/local/bin/sqlite3 /data/freenas-v1.db "update system_advanced set adv_serialconsole = 1"
    SERIALSPEED=`kenv hw.uart.console | sed -En 's/.*br:([0-9]+).*/\1/p'`
    if [ -n "$SERIALSPEED" ] ; then
       echo "comconsole_speed=\"$SERIALSPEED\"" >> ${_mnt}/boot/loader.conf
       chroot ${_mnt} /usr/local/bin/sqlite3 /data/freenas-v1.db "update system_advanced set adv_serialspeed = $SERIALSPEED"
    fi
    SERIALPORT=`kenv hw.uart.console | sed -En 's/.*io:([0-9a-fx]+).*/\1/p'`
    if [ -n "$SERIALPORT" ] ; then
       chroot ${_mnt} /usr/local/bin/sqlite3 /data/freenas-v1.db "update system_advanced set adv_serialport = '$SERIALPORT'"
    fi
}

mount_disk()
{
	local _mnt

	if [ $# -ne 1 ]; then
		return 1
	fi

	_mnt="$1"
	mkdir -p "${_mnt}"
	mount -t zfs -o noatime freenas-boot/ROOT/${BENAME} ${_mnt}
	mkdir -p ${_mnt}/data
	return 0
}

create_partitions()
{
    local _disk="$1"
    local _size="$2"

    if [ -n "${_size}" ]; then
	# Round ZFS partition size down to a multiple of 16 MiB (2^24),
	# leaving units in MiB (2^20).
	_size="-s $(( (_size >> 24) << 4 ))m"
    fi
    if gpart create -s GPT ${_disk}; then
	if [ "$BOOTMODE" = "UEFI" ] ; then
	  # EFI Mode
	  sysctl kern.geom.debugflags=16
	  sysctl kern.geom.label.disk_ident.enable=0
	  if gpart add -s 260m -t efi ${_disk}; then
	    if ! newfs_msdos -F 16 /dev/${_disk}p1 ; then
	      return 1
	    fi
	  fi
	else
	  # BIOS Mode
          if ! gpart add -t freebsd-boot -i 1 -s 512k ${_disk}; then
	    return 1
	  fi
	fi

	if is_swap_safe; then
	    gpart add -t freebsd-swap -a 4k -s 16g -i 3 ${_disk}
	fi
	if gpart add -t freebsd-zfs -a 4k -i 2 ${_size} ${_disk}; then
	    return 0
	fi
    fi
    return 1
}

get_minimum_size()
{
    local _min=0
    local _disk
    local _size

    for _disk
    do
	_size=""
	if create_partitions ${_disk} 1>&2; then
	    _size=$(diskinfo ${_disk}p2 | cut -f 3)
	    gmirror destroy -f swap || true
	    gpart destroy -F ${_disk} 1>&2
	fi
	if [ -z "${_size}" ]; then
	    echo "Could not do anything with ${_disk}, skipping" 1>&2
	    continue
	fi
	if [ ${_min} -eq 0 -o ${_size} -lt ${_min} ]; then
	    _min=${_size}
	fi
    done

    echo ${_min}
}

# Minimum required space for an installation.
# Docs state 8 GiB is the bare minimum, but we specify 8 GB here for wiggle room.
# That should leave enough slop for alignment, boot partition, etc.
: ${MIN_ZFS_PARTITION_SIZE:=$((8 * GB))}; readonly MIN_ZFS_PARTITION_SIZE

partition_disks()
{
    local _disks _disksparts
    local _mirror
    local _minsize
    local _size

    _disks=$*

    check_is_swap_safe ${_disks}

    gmirror destroy -f swap || true

    # Erase both typical metadata area.
    for _disk in ${_disks}; do
	gpart destroy -F ${_disk} >/dev/null 2>&1 || true
	dd if=/dev/zero of=/dev/${_disk} bs=1m count=2 >/dev/null
	_size=$(diskinfo ${_disk} | cut -f 3)
	dd if=/dev/zero of=/dev/${_disk} bs=1m oseek=$((_size / MiB - 2)) >/dev/null || true
    done

    _minsize=$(get_minimum_size ${_disks})

    if [ ${_minsize} -lt ${MIN_ZFS_PARTITION_SIZE} ]; then
	echo "Disk is too small to install ${PROJECT}" 1>&2
	return 1
    fi

    _disksparts=$(for _disk in ${_disks}; do
	create_partitions ${_disk} ${_minsize} >&2
	if [ "$BOOTMODE" != "UEFI" ] ; then
	    # Make the disk active
	    gpart set -a active ${_disk} >&2
	fi
	echo ${_disk}p2
    done)

    if [ $# -gt 1 ]; then
	_mirror="mirror"
    else
	_mirror=""
    fi
    zpool create -f -o cachefile=/tmp/zpool.cache -O mountpoint=none -O atime=off -O canmount=off freenas-boot ${_mirror} ${_disksparts}
    zfs set compression=on freenas-boot
    zfs create -o canmount=off freenas-boot/ROOT
    zfs create -o mountpoint=legacy freenas-boot/ROOT/${BENAME}

    return 0
}

disk_is_freenas()
{
    local _disk="$1"
    local _rv=1
    local upgrade_style=""
    local os_part=""
    local data_part=""

    # We have two kinds of potential upgrades here.
    # The old kind, with 4 slices, and the new kind,
    # with two partitions.

    mkdir -p /tmp/data_old
    if [ -c /dev/${_disk}s4 ]; then
	os_part=/dev/${_disk}s1a
	data_part=/dev/${_disk}s4
	upgrade_style="old"
    elif [ -c /dev/${_disk}p2 ]; then
	os_part=/dev/${_disk}p2
	upgrade_style="new"
    else
	return 1
    fi

    if [ "${upgrade_style}" = "new" ]; then
	# This code is very clumsy.  There
	# should be a way to structure it such that
	# all of the cleanup happens as we want it to.
	zdb -l ${os_part} | grep -qF "name: 'freenas-boot'" || return 1
	zpool import -N -f freenas-boot || return 1

	# Now we want to figure out which dataset to use.
	DS=$(zpool list -H -o bootfs freenas-boot | head -n 1 | cut -d '/' -f 3)
	if [ -z "$DS" ]; then
	    zpool export freenas-boot || true
	    return 1
	elif mount -t zfs freenas-boot/ROOT/"${DS}" /tmp/data_old; then
	    # If the active dataset doesn't have a database file,
	    # then it's not FN as far as we're concerned (the upgrade code
	    # will go badly).
	    # We also check for the Corral database directory.
	    if [ ! -f /tmp/data_old/data/freenas-v1.db -o \
		   -d /tmp/data_old/data/freenas.db ]; then
		umount /tmp/data_old || true
		zpool export freenas-boot || true
		return 1
	    fi
	    cp -pR /tmp/data_old/data/. /tmp/data_preserved
	    # Don't want to keep the old pkgdb around, since we're
	    # nuking the filesystem
	    rm -rf /tmp/data_preserved/pkgdb
	    if [ -f /tmp/data_old/conf/base/etc/hostid ]; then
		cp -p /tmp/data_old/conf/base/etc/hostid /tmp/
	    fi
	    if [ -d /tmp/data_old/root/.ssh ]; then
		cp -pR /tmp/data_old/root/.ssh /tmp/
	    fi
	    if [ -d /tmp/data_old/boot/modules ]; then
		mkdir -p /tmp/modules
		for i in `ls /tmp/data_old/boot/modules`
		do
		    cp -p /tmp/data_old/boot/modules/$i /tmp/modules/
		done
	    fi
	    if [ -d /tmp/data_old/usr/local/fusionio ]; then
		cp -pR /tmp/data_old/usr/local/fusionio /tmp/
	    fi
	    if [ -f /tmp/data_old/boot.config ]; then
		cp /tmp/data_old/boot.config /tmp/
	    fi
	    if [ -f /tmp/data_old/boot/loader.conf.local ]; then
		cp /tmp/data_old/boot/loader.conf.local /tmp/
	    fi
	    umount /tmp/data_old || return 1
	    zpool export freenas-boot || return 1
	    return 0
	fi # elif mount ...
    fi # if [ "${upgrade_style}" = "new" ]

    # This is now legacy code, to support the old
    # partitioning scheme (freenas-9.2 and earlier)
    if ! mount "${data_part}" /tmp/data_old ; then
	return 1
    fi

    ls /tmp/data_old > /tmp/data_old.ls
    if [ -f /tmp/data_old/freenas-v1.db ]; then
	_rv=0
    fi
    # XXX side effect, shouldn't be here!
    cp -pR /tmp/data_old/. /tmp/data_preserved
    umount /tmp/data_old
    if [ $_rv -eq 0 ]; then
	# For GUI upgrades, we only have one OS partition
	# that has conf/base/etc.  For ISO upgrades, we
	# have two, but only one is active.
	slice=$(gpart show ${_disk} | awk '/\[active\]/ { print $3 }')
	if [ -z "${slice}" ]; then
	    # We don't have an active slice, so something is wrong.
	    return 1
	fi
	mount /dev/${_disk}s${slice}a /tmp/data_old
	ls /tmp/data_old > /dev/null
	if [ ! -d /tmp/data_old/conf/base/etc ]
	then
	    # Mount the other partition
	    if [ "${slice}" -eq 1 ]; then
		slice=2
	    else
		slice=1
	    fi
	    umount /tmp/data_old
	    mount /dev/${_disk}s${slice}a /tmp/data_old
	    ls /tmp/data_old > /dev/null
	fi
	if [ -f /tmp/data_old/conf/base/etc/hostid ]; then
	    cp -p /tmp/data_old/conf/base/etc/hostid /tmp/
	fi
	if [ -d /tmp/data_old/root/.ssh ]; then
	    cp -pR /tmp/data_old/root/.ssh /tmp/
	fi
	if [ -d /tmp/data_old/boot/modules ]; then
	    mkdir -p /tmp/modules
	    for i in `ls /tmp/data_old/boot/modules`
	    do
		cp -p /tmp/data_old/boot/modules/$i /tmp/modules/
	    done
	fi
	if [ -d /tmp/data_old/usr/local/fusionio ]; then
	    cp -pR /tmp/data_old/usr/local/fusionio /tmp/
	fi
	if [ -f /tmp/data_old/boot.config ]; then
	    cp /tmp/data_old/boot.config /tmp/
	fi
	if [ -f /tmp/data_old/boot/loader.conf.local ]; then
	    cp /tmp/data_old/boot/loader.conf.local /tmp/
	fi
	umount /tmp/data_old
    fi
    rmdir /tmp/data_old
    return $_rv
}

prompt_password()
{

    local values value password="" password1 password2 _counter _tmpfile="/tmp/pwd.$$"

    cat << __EOF__ > /tmp/dialogconf
bindkey formfield TAB FORM_NEXT
bindkey formfield DOWN FORM_NEXT
bindkey formfield UP FORM_PREV
bindkey formbox DOWN FORM_NEXT
bindkey formbox TAB FORM_NEXT
bindkey formbox UP FORM_PREV
__EOF__

    export DIALOGRC="/tmp/dialogconf"

    while true; do
	dialog --insecure \
	    --output-fd 3 \
	    --visit-items \
	    --passwordform "Enter your root password; cancel for no root password" \
	    10 50 0 \
	    "Password:" 1 1 "" 0 20 25 50 \
	    "Confirm Password:" 2 1 "" 2 20 25 50 \
	    3> ${_tmpfile}

	if [ $? -ne 0 ]; then
	    rm -f ${_tmpfile}
	    return 1
	fi

	{ read password1 ; read password2; } < ${_tmpfile}
	rm -f ${_tmpfile}

	if [ "${password1}" != "${password2}" ]; then
	    dialog --msgbox "Passwords do not match." 7 60 2> /dev/null
	elif [ -z "${password1}" ]; then
	    dialog --msgbox "Empty password is not secure" 7 60 2> /dev/null
	else
	    password="${password1}"
	    break
	fi

    done

    rm -f ${DIALOGRC}
    unset DIALOGRC


    echo -n "${password}" 1>&2

}

create_be()
{
  if [ $# -ne 1 ]; then
    return 1
  fi

  echo "Creating new Boot-Environment"

  # When upgrading, we will simply create a new BE dataset and install
  # fresh into that, so old datasets are not lost
  zpool import -N -f freenas-boot || return 1

  # Create the new BE
  zfs create -o mountpoint=legacy freenas-boot/ROOT/${BENAME} || return 1

  # Mount the new BE datasets
  mkdir -p ${1}
  mount -t zfs freenas-boot/ROOT/${BENAME} ${1} || return 1
  mkdir -p ${1}/data

  return 0
}

menu_install()
{
    local _action
    local _disklist
    local _tmpfile
    local _answer
    local _cdlist
    local _items
    local _disk
    local _disks=""
    local _realdisks=""
    local _disk_old
    local _config_file
    local _desc
    local _list
    local _msg
    local _satadom
    local _i
    local _do_upgrade=""
    local _menuheight
    local _msg
    local _dlv
    local _password
    local os_part
    local data_part
    local upgrade_style="new"
    local whendone=""
    
    local readonly CD_UPGRADE_SENTINEL="/data/cd-upgrade"
    local readonly NEED_UPDATE_SENTINEL="/data/need-update"
    # create a sentinel file for post-fresh-install boots
    local readonly FIRST_INSTALL_SENTINEL="/data/first-boot"
    local readonly TRUENAS_EULA_PENDING_SENTINEL="/data/truenas-eula-pending"
    local readonly POOL="freenas-boot"

    _tmpfile="/tmp/answer"
    TMPFILE=$_tmpfile
    REALDISKS="/tmp/realdisks"

    while getopts "U:P:X:" opt; do
	case "${opt}" in
	    U)	if ${OPTARG}; then _do_upgrade=1 ; else _do_upgrade=0; fi
		;;
	    P)	_password="${OPTARG}"
		;;
	    X)	case "${OPTARG}" in
		    reboot)	whendone=reboot ;;
		    "wait")	whendone=wait ;;
		    halt)	whendone=halt ;;
		    *)		whendone="" ;;
		esac
		;;
	    *)	echo "Unknown option ${opt}" 1>&2
		;;
	esac
    done
    shift $((OPTIND-1))
    
    if [ $# -gt 0 ]
    then
	_disks="$@"
	INTERACTIVE=false
    else
	INTERACTIVE=true
    fi

    if ${INTERACTIVE}; then
	pre_install_check || return 0
    fi
    
    if do_sata_dom
    then
	_satadom="YES"
    else
	_satadom=""
	if ${INTERACTIVE}; then
	    get_physical_disks_list
	    _disklist="${VAL}"

	    _list=""
	    _items=0
	    for _disk in ${_disklist}; do
		_desc=$(get_media_description "${_disk}" | sed "s/'/'\\\''/g")
		_list="${_list} ${_disk} '${_desc}' off"
		_items=$((${_items} + 1))
	    done
	    
	    _tmpfile="/tmp/answer"
	    if [ ${_items} -ge 10 ]; then
		_items=10
		_menuheight=20
	    else
		_menuheight=9
		_menuheight=$((${_menuheight} + ${_items}))
	    fi
	    if [ "${_items}" -eq 0 ]; then
		# Inform the user
		eval "dialog --title 'Choose destination media' --msgbox 'No drives available' 5 60" 2>${_tmpfile}
		return 0
	    fi

	    eval "dialog --title 'Choose destination media' \
	      --checklist 'Select one or more drives where ${PROJECT} should be installed (use arrow keys to navigate to the drive(s) for installation; select a drive with the spacebar).' \
	      ${_menuheight} 60 ${_items} ${_list}" 2>${_tmpfile}
	    [ $? -eq 0 ] || exit 1
	fi
    fi # ! do_sata_dom

    if [ -f "${_tmpfile}" ]; then
	_disks=$(eval "echo `cat "${_tmpfile}"`")
	rm -f "${_tmpfile}"
    fi

    if [ -z "${_disks}" ]; then
	${INTERACTIVE} && dialog --msgbox "You need to select at least one disk!" 6 74
	exit 1
    fi

    if disk_is_mounted ${_disks} ; then
        ${INTERACTIVE} && dialog --msgbox "The destination drive is already in use!" 6 74
        exit 1
    fi

    _action="installation"
    _upgrade_type="format"
    # This needs to be re-done.
    # If we're not interactive, then we have
    # to assume _disks is correct.
    # If we do have more than one disk given,
    # we should also do something if they're all
    # freenas disks.  But how do we figure out which
    # one to use?  The current code in disk_is_freenas
    # is very, very heavy -- it actually backs up the
    # data from a freenas installation.  It also does
    # a zpool import.
    for _disk in ${_disks}; do
    if disk_is_freenas ${_disk} ; then
        if ${INTERACTIVE}; then
	    if ask_upgrade ${_disk} ; then
		_do_upgrade=1
		_action="upgrade"
	    fi
	else
	    if [ "${_do_upgrade}" != "0" ]; then
		_do_upgrade=1
		_action="upgrade"
	    fi
	fi
	if [ -c /dev/${_disk}s4 ]; then
	    upgrade_style="old"
	elif [ -c /dev/${_disk}p2 ]; then
	    upgrade_style="new"
	else
	    echo "Unknown upgrade style" 1>&2
	    exit 1
	fi
	# Ask if we want to do a format or inplace upgrade
        if ${INTERACTIVE}; then
	    if ask_upgrade_inplace ; then
		_upgrade_type="inplace"
	    fi
	fi
	break
    elif [ "${_satadom}" = "YES" -a -c /dev/ufs/TrueNASs4 ]; then
	# Special hack for USB -> DOM upgrades
	_disk_old=`glabel status | awk '/ ufs\/TrueNASs4 / { print $3 }' | sed -e 's,s4$,,g'`
	if disk_is_freenas ${_disk_old} ; then
	    if ask_upgrade ${_disk_old} ; then
		_do_upgrade=2
		_action="upgrade"
		break
	    fi
	fi
    fi
    done
    # If we haven't set _do_upgrade by now, we're not
    # doing an upgrade.
    if [ -z "${_do_upgrade}" ]; then
	_do_upgrade=0
    fi

    if [ "${_satadom}" = "YES" -a -n "$(echo ${_disks}|grep "raid/")" ]; then
	_realdisks=$(cat ${REALDISKS})
    else
	_realdisks=$_disks
    fi


    ${INTERACTIVE} && new_install_verify "$_action" "$_upgrade_type" ${_realdisks}
    _config_file="/tmp/pc-sysinstall.cfg"

    if ${INTERACTIVE} && [ "${_do_upgrade}" -eq 0 ]; then
	prompt_password 2> /tmp/password
	if [ $? -eq 0 ]; then
	    _password="$(cat /tmp/password 2> /dev/null)"
	fi
    fi

    if [ ${_do_upgrade} -eq 0 ]; then
	# With the new partitioning, disk_is_freenas may
	# copy /data.  So if we don't need it, remove it,
	# or else it'll do an update anyway.  Oops.
	rm -rf /tmp/data_preserved
    fi
    # Start critical section.
    if ${INTERACTIVE}; then
	trap "set +x; read -p \"The ${PROJECT} $_action on ${_realdisks} has failed. Press enter to continue.. \" junk" EXIT
    else
#	trap "echo \"The ${PROJECT} ${_action} on ${_realdisks} has failed.\" ; sleep 15" EXIT
	trap "set +x; read -p \"The ${PROJECT} $_action on ${_realdisks} has failed. Press enter to continue.. \" junk" EXIT
    fi
    set -e
#    set -x

    #  _disk, _image, _config_file
    # we can now build a config file for pc-sysinstall
    # build_config  ${_disk} "$(get_image_name)" ${_config_file}

    if [ ${_do_upgrade} -eq 1 ]
    then
        /etc/rc.d/dmesg start
        mkdir -p /tmp/data
	if [ "${upgrade_style}" = "old" ]; then
	    # For old style, we have two potential
	    # partitions to look at:  s1a and s2a.
	    # 
	    slice=$(gpart show ${_disk} | awk '/\[active\]/ { print $3 }')
	    if [ -z "${slice}" ]; then
		# We don't have an active slice, so something is wrong.
		false
	    fi
	    mount /dev/${_disk}s${slice}a /tmp/data
	    ls /tmp/data > /dev/null
	    if [ ! -d /tmp/data/conf/base/etc ]
	    then
		# Mount the other partition
		if [ "${slice}" -eq 1 ]; then
		    slice=2
		else
		    slice=1
		fi
		umount /tmp/data
		mount /dev/${_disk}s${slice}a /tmp/data
		ls /tmp/data > /dev/null
	    fi
            umount /tmp/data
	elif [ "${upgrade_style}" != "new" ]; then
		echo "Unknown upgrade style" 1>&2
		false
	fi
        rmdir /tmp/data
    else
	# Destroy existing partition table, if there is any but tolerate
	# failure.
	for _disk in ${_realdisks}; do
	    gpart destroy -F ${_disk} || true
	done
    fi

    # Run pc-sysinstall against the config generated

    # Hack #1
    export ROOTIMAGE=1

    if [ ${_do_upgrade} -eq 1 -a ${upgrade_style} = "new" -a "${_upgrade_type}" = "inplace" ]
    then
      # Set the boot-environment name
      BENAME="default-`date +%Y%m%d-%H%M%S`"
      export BENAME

      # When doing new-style upgrades, we can keep the old zpool
      # and instead do a new BE creation
      create_be /tmp/data
    else
      # Set the boot-environment name
      BENAME="default"
      export BENAME

      if echo ${_disks} | grep -q "raid/"; then
	graid delete ${_disks}
      fi

      if [ -n "$(kenv grub.platform 2>/dev/null)" ] ; then
        if [ "$(kenv grub.platform)" = "efi" ] ; then
          BOOTMODE="UEFI"
        else
          BOOTMODE="BIOS"
	fi
      else
        BOOTMODE=$(sysctl -n machdep.bootmethod)
      fi
      if ${INTERACTIVE}; then
        # Prompt for UEFI or BIOS mode
        if ask_boot_method
        then
          BOOTMODE="UEFI"
	else
	  BOOTMODE="BIOS"
        fi
      fi
      export BOOTMODE

      # We repartition on fresh install, or old upgrade_style
      # This destroys all of the pool data, and
      # ensures a clean filesystems.
      partition_disks ${_realdisks}
      mount_disk /tmp/data
    fi

    if [ -d /tmp/data_preserved ]; then
	cp -pR /tmp/data_preserved/. /tmp/data/data
	# we still need the newer version we are upgrading to's
	# factory-v1.db, else issuing a factory-restore on the
	# newly upgraded system completely horks the system
	cp /data/factory-v1.db /tmp/data/data/
	chown www:www /tmp/data/data/factory-v1.db
    else
	chown -R www:www /tmp/data/data
    fi

    # First install the base packages
    mkdir -p /tmp/data/var/db/pkg
    export PKG_DBDIR="/tmp/data/var/db/pkg"

    for inspkg in os/userland os/kernel ports-mgmt/pkg
    do
        env ASSUME_ALWAYS_YES=YES pkg -r /tmp/data install -yf $inspkg
        if [ $? -ne 0 ] ; then
            echo "Failed installing ${inspkg}"
        fi
    done

    unset PKG_DBDIR

    # Set automatic flag for base packages explicitly
    chroot /tmp/data pkg set -y -A 00 os/userland
    chroot /tmp/data pkg set -y -A 00 os/kernel

    # beadm will need a devfs and we need to mount it before we install packages as well
    mount -t devfs devfs /tmp/data/dev

    # Setup pkg appropriately
    mkdir /tmp/data/dist
    mount_nullfs /dist /tmp/data/dist
    mount_nullfs /etc/pkg /tmp/data/etc/pkg

    PACKAGE_BUILDING=yes
    export PACKAGE_BUILDING

    for i in $(jq -r '."iso"."'auto-install-packages'"."'default'" | join(" ")' /var/db/trueos-manifest.json)
    do
        echo "Installing package: ${i}"
        pkg -c /tmp/data install -y ${i}
        if [ $? -ne 0 ] ; then
            echo "Failed installing: ${i}"
        fi
    done

    chroot /tmp/data /usr/sbin/sysrc -f /boot/loader.conf rc_system=rc
    chroot /tmp/data /usr/bin/touch /etc/dynamicdiskless
    chroot /tmp/data /bin/mkdir -p /conf/base/etc
    chroot /tmp/data /bin/sh -c '/bin/echo 65535 > /conf/base/etc/md_size'
    chroot /tmp/data /bin/mkdir -p /conf/base/var
    chroot /tmp/data /bin/mkdir -p /conf/base/mnt
    chroot /tmp/data /bin/sh -c '/bin/echo 8192 > /conf/base/mnt/md_size'
    chroot /tmp/data /bin/mkdir -p /conf/base/usr__local__etc
    chroot /tmp/data /bin/sh -c '/bin/echo 65535 > /conf/base/usr__local__etc/md_size'
    chroot /tmp/data /bin/ln -s /usr/local/etc /etc/local
    chroot /tmp/data /bin/ln -fs /var/tmp/.rnd /.rnd

    rm -f /tmp/data/conf/default/etc/fstab /tmp/data/conf/base/etc/fstab
    ln /tmp/data/etc/fstab /tmp/data/conf/base/etc/fstab || echo "Cannot link fstab"
    if [ "${_do_upgrade}" -ne 0 ]; then
	if [ -f /tmp/hostid ]; then
            cp -p /tmp/hostid /tmp/data/conf/base/etc
	fi
	if [ -d /tmp/.ssh ]; then
            cp -pR /tmp/.ssh /tmp/data/root/
	fi
	
	# TODO: this needs to be revisited.
	if [ -d /tmp/modules ]; then
            for i in `ls /tmp/modules`
            do
		# If it already exists, simply don't copy it.
		cp -np /tmp/modules/$i /tmp/data/boot/modules || true
            done
	fi
	if [ -d /tmp/fusionio ]; then
            cp -pR /tmp/fusionio /tmp/data/usr/local/
	fi
	if [ -f /tmp/boot.config ]; then
	    cp /tmp/boot.config /tmp/data/
	fi
	if [ -f /tmp/loader.conf.local ]; then
	    cp /tmp/loader.conf.local /tmp/data/boot/
	    # TODO: #7042 - Don't use debug kernel
	    sed -i '' -e 's,^module_path=.*,module_path="/boot/kernel;/boot/modules;/usr/local/modules",g' \
		-e '/^kernel=.*/d' /tmp/data/boot/loader.conf /tmp/data/boot/loader.conf.local
	fi
    fi
    
    # To support Xen, we need to disable HPET.
    if [ "$(/tmp/data/usr/local/sbin/dmidecode -s system-product-name)" = "HVM domU" ]; then
	if ! grep -q 'hint.hpet.0.clock' /tmp/data/boot/loader.conf.local 2>/dev/null ; then
	    echo 'hint.hpet.0.clock="0"' >> /tmp/data/boot/loader.conf.local
	fi
    fi
    # Debugging pause.
    # read foo
    
    # XXX: Fixup
    # tar cf - -C /tmp/data/conf/base etc | tar xf - -C /tmp/data/

    # Create a temporary /var
    mount -t tmpfs tmpfs /tmp/data/var
    chroot /tmp/data /usr/sbin/mtree -deUf /etc/mtree/BSD.var.dist -p /var
    # We need this hack due to sqlite3 called from rc.conf.local.
    chroot /tmp/data /sbin/ldconfig /usr/local/lib
    chroot /tmp/data /etc/rc.d/ldconfig forcestart
    # Save current serial console settings into database.
    save_serial_settings /tmp/data
    # Set default boot filesystem
    zpool set bootfs=freenas-boot/ROOT/${BENAME} freenas-boot
    install_loader /tmp/data ${_realdisks}
    
#    set +x
    if [ -d /tmp/data_preserved ]; then
	# Instead of sentinel files, let's just migrate!
	# Unfortunately, this doesn't seem to work well.
	# This should be investigated.
#	chroot /tmp/data /bin/sh -c "/usr/bin/yes | \
#		/usr/local/bin/python
#		      /usr/local/www/freenasUI/manage.py migrate --all --merge"
	# Create upgrade sentinel files
	: > /tmp/data/${CD_UPGRADE_SENTINEL}
	: > /tmp/data/${NEED_UPDATE_SENTINEL}
	${INTERACTIVE} && dialog --msgbox "The installer has preserved your database file.
#${PROJECT} will migrate this file, if necessary, to the current format." 6 74
    elif [ "${_do_upgrade}" -eq 0 ]; then
	if [ -n "${_password}" ]; then
		# Set the root password
		chroot /tmp/data /etc/netcli reset_root_pw "${_password}"
	fi
    fi
    : > /tmp/data/${FIRST_INSTALL_SENTINEL}
    if is_truenas && [ "${_do_upgrade}" -eq 0 ]; then
        : > /tmp/data/${TRUENAS_EULA_PENDING_SENTINEL}
    fi

    chroot /tmp/data /bin/rm -rf /tmp
    chroot /tmp/data /bin/ln -fs /var/tmp /tmp

    # Finally, before we unmount, start a scrub.
    # zpool scrub freenas-boot || true

    umount -f /tmp/data/dist
    umount -f /tmp/data/etc/pkg

    umount /tmp/data/dev
    umount /tmp/data/var
    umount /tmp/data/

    # End critical section.
    set +e

    trap - EXIT

    _msg="The ${PROJECT} $_action on ${_realdisks} succeeded!\n"
    _dlv=`/sbin/sysctl -n vfs.nfs.diskless_valid 2> /dev/null`
    if [ ${_dlv:=0} -ne 0 ]; then
        _msg="${_msg}Please reboot, and change BIOS boot order to *not* boot over network."
    else
        _msg="${_msg}Please reboot and remove the installation media."
    fi
    if ${INTERACTIVE}; then
	dialog --msgbox "$_msg" 6 74
    elif [ -n "${whendone}" ]; then
	case "${whendone}" in
	    halt)	halt -p ;;
	    "wait")	dialog --msgbox "$_msg" 6 74 ;;
	esac
	return 0
    fi

    return 0
}

menu_shell()
{
    /bin/sh
}

menu_reboot()
{
    echo "Rebooting..."
    reboot >/dev/null
}

menu_shutdown()
{
    echo "Halting and powering down..."
    halt -p >/dev/null
}

#
# Use the following kernel environment variables
#
#  test.nfs_mount -  The NFS directory to mount to get
#                    access to the test script.
#  test.script    -  The path of the test script to run,
#                    relative to the NFS mount directory.
#  test.run_tests_on_boot - If set to 'yes', then run the
#                           tests on bootup, before displaying
#                           the install menu. 
#
#  For example, if the following variables are defined:
#
#    test.nfs_mount=10.5.0.24:/usr/jails/pxeserver/tests
#    test.script=/tests/run_tests.sh
#
#  Then the system will execute the following:
#     mount -t nfs 10.5.0.24:/usr/jails/pxeserver/tests /tmp/tests
#     /tmp/tests/tests/run_tests.sh
menu_test()
{
    local _script
    local _nfs_mount

    _script="$(kenv test.script 2> /dev/null)"
    _nfs_mount="$(kenv test.nfs_mount 2> /dev/null)"
    if [ -z "$_script" -o -z "$_nfs_mount"  ]; then
        return
    fi
  
    if [ -e /tmp/tests ]; then
        umount /tmp/tests 2> /dev/null
        rm -fr /tmp/tests
    fi 
    mkdir -p /tmp/tests
    if [ ! -d /tmp/tests ]; then
        echo "No test directory"
        wait_keypress
    fi
    umount /tmp/tests 2> /dev/null
    mount -t nfs -o ro "$_nfs_mount" /tmp/tests
    if [ ! -e "/tmp/tests/$_script" ]; then
        echo "Cannot find /tmp/tests/$_script"
        wait_keypress
        return
    fi

    dialog --stdout --prgbox /tmp/tests/$_script 15 80
}

main()
{
    local _tmpfile="/tmp/answer"
    local _number
    local _test_option=

    if [ $# -gt 0 ]; then
	# $1 will have the device name
	menu_install "$@"
	exit $?
    fi

    case "$(kenv test.run_tests_on_boot 2> /dev/null)" in
    [Yy][Ee][Ss])
        menu_test
        ;;
    esac

    if [ -n "$(kenv test.script 2> /dev/null)" ]; then
        _test_option="5 Test"
    fi

    while :; do

        dialog --clear --title "${PROJECT} ${VERSION} Console Setup" --menu "" 12 73 6 \
            "1" "Install/Upgrade" \
            "2" "Shell" \
            "3" "Reboot System" \
            "4" "Shutdown System" \
            $_test_option \
            2> "${_tmpfile}"
        _number=`cat "${_tmpfile}"`
        case "${_number}" in
            1) menu_install ;;
            2) menu_shell ;;
            3) menu_reboot ;;
            4) menu_shutdown ;;
            5) menu_test ;;
        esac
    done
}

if is_truenas ; then
    . "$(dirname "$0")/install_sata_dom.sh"
fi

# Parse a config file.
# We don't do much in the way of error checking.
# Format is very simple:
# <opt>=<value>
# <value> may be a list (e.g., disk devices)
# The output is suitable to be used as the arguments
# to main(), which will directl ycall menu_install().

yesno()
{
    # Output "true" or "false" depending on the argument
    if [ $# -ne 1 ]; then
	echo "false"
	return 0
    fi
    case "$1" in
	[yY][eE][sS] | [tT][rR][uU][eE])	echo true ;;
	*)	echo false;;
    esac
    return 0
}

getsize()
{
    # Given a size specifier, convert it to bytes.
    # No suffix, or a suffix of "[bBcC]", means bytes;
    # [kK] is 1024, etc.
    if [ $# -ne 1 ]; then
	echo 0
	return 0
    fi
    case "$1" in
	*[bB][cC])	expr "$1" : "^\([0-9]*\)[bB][cC]" || echo 0;;
	*[kK])	expr $(expr "$1" : "^\([0-9]*\)[kK]") \* 1024 || echo 0;;
	*[mM])	expr $(expr "$1" : "^\([0-9]*\)[gG]") \* 1024 \* 1024 || echo 0;;
	*[gG])	expr $(expr "$1" : "^\([0-9]*\)[gG]") \* 1024 \* 1024 \* 1024 || echo 0;;
	*[tT])	expr $(expr "$1" : "^\([0-9]*\)[tT]") \* 1024 \* 1024 \* 1024 \* 1024 || echo 0;;
	*) expr "$1" : "^\([0-9]*\)$" || echo 0;;
    esac
    return 0
}
	
parse_config()
{
    local _conf="/etc/install.conf"
    local _diskList=""
    local _minSize=""
    local _maxSize=""
    local _mirror=false
    local _forceMirror=false
    local _upgrade=""
    local _output=""
    local _maxDisks=""
    local _cmd
    local _args
    local _boot
    local _disk
    local _diskSize
    local _diskCount=0
    local password=""
    local whenDone=""
    
    while read line
    do
	if expr "${line}" : "^#" > /dev/null
	then
	    continue
	fi
	if ! expr "${line}" : "^[a-zA-Z]*=" > /dev/null
	then
	    continue
	fi
	_cmd=$(expr "${line}" : "^\([^=]*\)=.*")
	_args=$(expr "${line}" : "^[^=]*=\(.*\)$")
	case "${_cmd}" in
	    password)	password="${_args}" ;;
	    whenDone)	case "${_args}" in
			    reboot | wait | halt)	whenDone=${_args} ;;
			    *)	whenDone="" ;;
			esac
			;;
	    minDiskSize)	_minSize=$(getsize "${_args}") ;;
	    maxDiskSize)	_maxSize=$(getsize "${_args}") ;;
	    diskCount)		_maxDisks=${_args} ;;
	    upgrade)	_upgrade=$(yesno "${_args}") ;;
	    disk|disks)	_diskList="${_args}" ;;
	    mirror)	case "${_args}" in
			    [fF][oO][rR][cC][eE])	_mirror=true ; _forceMirror=true ;;
			    *)	_mirror=$(yesno "${_args}") ;;
			esac
			;;
	esac
    done < ${_conf}
    # Okay, done reading the config file
    # Now to go through and handle the settings.
    # Order is important here.
    # But the first thing we want to do is determine our
    # boot disk, so we can exclude it later.
    # For the install, the mount situation is complex,
    # but we want to look for a label of "INSTALL" and find
    # out the device for that.
    _boot=$(glabel status | awk '/INSTALL/ { print $3 }')
    if [ -n "${_upgrade}" ]; then
	# Option to do an upgrade
	_output="-U ${_upgrade}"
    fi
    if [ -n "${password}" ]; then
	# Set the root password
	_output="${_output} -P \"${password}\""
    fi
    if [ -n "${whenDone}" ]; then
	# What to do when finished installing
	_output="${_output} -X ${whenDone}"
    fi
    if [ -z "${_diskList}" ]; then
	# No disks specified in the config file
	# So just get the list from the kernel.
	# We'll be filtering it below
	_diskList=$(sysctl -n kern.disks)
    fi
    for _disk in ${_diskList}
    do
	if [ "${_disk}" = "${_boot}" ]; then
	    continue
	fi
	_diskSize=$(diskinfo ${_disk} | cut -f 3)
	if [ -n "${_minSize}" ] && [ "${_diskSize}" -lt "${_minSize}" ]; then
	    continue
	fi
	if [ -n "${_maxSize}" ] && [ "${_diskSize}" -gt "${_maxSize}" ]; then
	    continue
	fi
	_output="${_output} ${_disk}"
	_diskCount=$(expr ${_diskCount} + 1)
	if ! ${_mirror}; then
	    break
	fi
	if [ -n "${_maxDisks}" ] && [ "${_diskCount}" -eq "${_maxDisks}" ]; then
	    break;
	fi
    done
    # Now we should have some disks.
    if [ ${_diskCount} -eq 0 ]; then
	echo "No disks available that match the criteria" 1>&2
	return 1
    fi
    if ${_forceMirror} && [ ${_diskCount} -eq 1 ]; then
	echo "Only one disk found, but mirror required for install" 1>&2
	return 1
    fi
    echo ${_output}
    return 0
}

if [ -f /etc/install.conf ]; then
    CONFIG_OUTPUT=$(parse_config)
    if [ $? -ne 0 ]; then
	read -p "Config file parsing failed to find specified media " foo
	echo "Dropping into a shell"
	/bin/sh
	exit 1
    elif [ -n "${CONFIG_OUTPUT}" ]; then
	set -- ${CONFIG_OUTPUT}
	menu_install "$@"
	menu_reboot
    fi
fi

main "$@"
