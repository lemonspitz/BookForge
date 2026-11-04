// The parts of libmtp's data structures Book Forge reads. The functions are looked up at run time
// (Readers.swift), so the app still starts if the bundled libmtp is missing.
#include <stdint.h>
#include <time.h>

typedef struct {
  char *vendor;
  uint16_t vendor_id;
  char *product;
  uint16_t product_id;
  uint32_t device_flags;
} BF_device_entry_t;

typedef struct {
  BF_device_entry_t device_entry;
  uint32_t bus_location;
  uint8_t devnum;
} BF_raw_device_t;

typedef struct BF_devicestorage_struct {
  uint32_t id;
  uint16_t StorageType;
  uint16_t FilesystemType;
  uint16_t AccessCapability;
  uint64_t MaxCapacity;
  uint64_t FreeSpaceInBytes;
  uint64_t FreeSpaceInObjects;
  char *StorageDescription;
  char *VolumeIdentifier;
  struct BF_devicestorage_struct *next;
  struct BF_devicestorage_struct *prev;
} BF_devicestorage_t;

// only the leading fields
typedef struct {
  uint8_t object_bitsize;
  void *params;
  void *usbinfo;
  BF_devicestorage_t *storage;
} BF_mtpdevice_t;

typedef struct BF_file_struct {
  uint32_t item_id;
  uint32_t parent_id;
  uint32_t storage_id;
  char *filename;
  uint64_t filesize;
  time_t modificationdate;
  int filetype;
  struct BF_file_struct *next;
} BF_file_t;
