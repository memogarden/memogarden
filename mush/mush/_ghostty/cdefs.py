"""
C function declarations for libghostty-vt binding.

This file contains the cffi cdef declarations for the libghostty-vt C API
at the pinned commit. When upstream churns, only this file changes.
"""

# Basic types and constants
cdefs = """
// From types.h
typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_SUCCESS = 0,
    GHOSTTY_OUT_OF_MEMORY = -1,
    GHOSTTY_INVALID_VALUE = -2,
    GHOSTTY_OUT_OF_SPACE = -3,
    GHOSTTY_NO_VALUE = -4,
    GHOSTTY_RESULT_MAX_VALUE = 2147483647,
} GhosttyResult;

typedef struct GhosttyTerminalImpl* GhosttyTerminal;

typedef struct {
    const uint8_t* ptr;
    size_t len;
} GhosttyString;

typedef struct {
    uint8_t* ptr;
    size_t cap;
    size_t len;
} GhosttyBuffer;

// From terminal.h
typedef struct {
    uint16_t cols;
    uint16_t rows;
    size_t max_scrollback;
    uint64_t _padding[8];
} GhosttyTerminalOptions;

typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_SCROLL_VIEWPORT_TOP,
    GHOSTTY_SCROLL_VIEWPORT_BOTTOM,
    GHOSTTY_SCROLL_VIEWPORT_DELTA,
    GHOSTTY_SCROLL_VIEWPORT_MAX_VALUE = 2147483647,
} GhosttyTerminalScrollViewportTag;

typedef union {
    intptr_t delta;
    uint64_t _padding[2];
} GhosttyTerminalScrollViewportValue;

typedef struct {
    GhosttyTerminalScrollViewportTag tag;
    GhosttyTerminalScrollViewportValue value;
} GhosttyTerminalScrollViewport;

typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_TERMINAL_SCREEN_PRIMARY = 0,
    GHOSTTY_TERMINAL_SCREEN_ALTERNATE = 1,
    GHOSTTY_TERMINAL_SCREEN_MAX_VALUE = 2147483647,
} GhosttyTerminalScreen;

typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_TERMINAL_CURSOR_STYLE_BAR = 0,
    GHOSTTY_TERMINAL_CURSOR_STYLE_BLOCK = 1,
    GHOSTTY_TERMINAL_CURSOR_STYLE_UNDERLINE = 2,
    GHOSTTY_TERMINAL_CURSOR_STYLE_MAX_VALUE = 2147483647,
} GhosttyTerminalCursorStyle;

typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_TERMINAL_OPT_WRITE_PTY,
    GHOSTTY_TERMINAL_OPT_BELL,
    GHOSTTY_TERMINAL_OPT_TITLE_CHANGED,
    GHOSTTY_TERMINAL_OPT_PWD_CHANGED,
    GHOSTTY_TERMINAL_OPT_ENQUIRY,
    GHOSTTY_TERMINAL_OPT_XTVERSION,
    GHOSTTY_TERMINAL_OPT_SIZE,
    GHOSTTY_TERMINAL_OPT_COLOR_SCHEME,
    GHOSTTY_TERMINAL_OPT_DEVICE_ATTRIBUTES,
    GHOSTTY_TERMINAL_OPT_USERDATA,
    GHOSTTY_TERMINAL_OPT_MAX_VALUE = 2147483647,
} GhosttyTerminalOption;

typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_TERMINAL_DATA_ROWS,
    GHOSTTY_TERMINAL_DATA_COLS,
    GHOSTTY_TERMINAL_DATA_WIDTH_PIXEL,
    GHOSTTY_TERMINAL_DATA_HEIGHT_PIXEL,
    GHOSTTY_TERMINAL_DATA_CELL_WIDTH_PIXEL,
    GHOSTTY_TERMINAL_DATA_CELL_HEIGHT_PIXEL,
    GHOSTTY_TERMINAL_DATA_TITLE,
    GHOSTTY_TERMINAL_DATA_PWD,
    GHOSTTY_TERMINAL_DATA_CURSOR_ROW,
    GHOSTTY_TERMINAL_DATA_CURSOR_COL,
    GHOSTTY_TERMINAL_DATA_CURSOR_VISIBLE,
    GHOSTTY_TERMINAL_DATA_CURSOR_STYLE,
    GHOSTTY_TERMINAL_DATA_SCREEN,
    GHOSTTY_TERMINAL_DATA_PRIMARY_SCREEN_SIZE,
    GHOSTTY_TERMINAL_DATA_ALTERNATE_SCREEN_SIZE,
    GHOSTTY_TERMINAL_DATA_KITTY_GRAPHICS,
    GHOSTTY_TERMINAL_DATA_MAX_VALUE = 2147483647,
} GhosttyTerminalData;

// Callback types
typedef void (*GhosttyTerminalWritePtyFn)(GhosttyTerminal, void*, const uint8_t*, uintptr_t);
typedef void (*GhosttyTerminalTitleChangedFn)(GhosttyTerminal, void*, const char*);
typedef void (*GhosttyTerminalPwdChangedFn)(GhosttyTerminal, void*, const char*);
typedef void (*GhosttyTerminalBellFn)(GhosttyTerminal, void*);
typedef const char* (*GhosttyTerminalEnquiryFn)(GhosttyTerminal, void*);
typedef bool (*GhosttyTerminalSizeFn)(GhosttyTerminal, void*, uint16_t*, uint16_t*);
typedef bool (*GhosttyTerminalDeviceAttributesFn)(GhosttyTerminal, void*, GhosttyString*);

// From allocator.h
typedef void* (*ghostty_alloc_fn)(void* userdata, size_t size);
typedef void (*ghostty_free_fn)(void* userdata, void* ptr);
typedef void* (*ghostty_realloc_fn)(void* userdata, void* ptr, size_t size);

typedef struct {
    void* userdata;
    ghostty_alloc_fn alloc;
    ghostty_free_fn free;
    ghostty_realloc_fn realloc;
} GhosttyAllocator;

// From modes.h
typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_MODE_APPLICATION_CURSOR_KEYS,
    GHOSTTY_MODE_BRACKETED_PASTE,
    GHOSTTY_MODE_MOUSE_TRACKING,
    GHOSTTY_MODE_ALTERNATE_SCREEN,
    GHOSTTY_MODE_MAX_VALUE = 2147483647,
} GhosttyMode;

// From grid_ref.h
typedef struct GhosttyGridRefImpl* GhosttyGridRef;

typedef struct {
    uint32_t fg;
    uint32_t bg;
    uint32_t decoration;
} GhosttyCell;

typedef struct {
    bool soft_wrapped;
    bool hyperlinked;
    uint64_t _padding[6];
} GhosttyRowInfo;

// From screen.h
typedef struct {
    uint16_t cursor_row;
    uint16_t cursor_col;
    bool cursor_visible;
    uint8_t cursor_style;
    bool alt_screen;
    bool bracketed_paste;
    bool mouse_reporting;
    bool application_cursor;
    uint64_t _padding[4];
} GhosttyScreenInfo;

// From formatter.h
typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_FORMATTER_FORMAT_PLAIN,
    GHOSTTY_FORMATTER_FORMAT_VT,
    GHOSTTY_FORMATTER_FORMAT_HTML,
    GHOSTTY_FORMATTER_FORMAT_MAX_VALUE = 2147483647,
} GhosttyFormatterFormat;

typedef struct GhosttyFormatterImpl* GhosttyFormatter;

typedef struct {
    size_t size;
    GhosttyFormatterFormat emit;
    bool trim;
    uint64_t _padding[8];
} GhosttyFormatterTerminalOptions;

// From paste.h
typedef bool (*GhosttyPasteIsSafeFn)(const uint8_t* data, size_t len);

// From build_info.h
typedef enum GHOSTTY_ENUM_TYPED {
    GHOSTTY_BUILD_INFO_COMMIT_HASH,
    GHOSTTY_BUILD_INFO_FEATURES,
    GHOSTTY_BUILD_INFO_MAX_VALUE = 2147483647,
} GhosttyBuildInfoField;

// Terminal lifecycle functions
GhosttyResult ghostty_terminal_new(const GhosttyAllocator* allocator, GhosttyTerminal* terminal, GhosttyTerminalOptions options);
void ghostty_terminal_free(GhosttyTerminal terminal);
void ghostty_terminal_reset(GhosttyTerminal terminal);
GhosttyResult ghostty_terminal_resize(GhosttyTerminal terminal, uint16_t cols, uint16_t rows, uint32_t cell_width_px, uint32_t cell_height_px);

// Terminal state functions
GhosttyResult ghostty_terminal_set(GhosttyTerminal terminal, GhosttyTerminalOption option, const void* value);
void ghostty_terminal_vt_write(GhosttyTerminal terminal, const uint8_t* data, size_t len);
GhosttyResult ghostty_terminal_mode_get(GhosttyTerminal terminal, GhosttyMode mode, bool* out_value);
void ghostty_terminal_scroll_viewport(GhosttyTerminal terminal, GhosttyTerminalScrollViewport behavior);

// Grid reference functions
GhosttyResult ghostty_terminal_grid_ref(GhosttyTerminal terminal, GhosttyGridRef* ref);
void ghostty_grid_ref_free(GhosttyGridRef ref);
GhosttyResult ghostty_grid_ref_cell(GhosttyGridRef ref, uint16_t row, uint16_t col, GhosttyCell* out);
GhosttyResult ghostty_grid_ref_row(GhosttyGridRef ref, uint16_t row, GhosttyRowInfo* out);

// Formatter functions
GhosttyResult ghostty_formatter_terminal_new(GhosttyTerminal terminal, GhosttyFormatter* fmt, GhosttyFormatterTerminalOptions opts);
void ghostty_formatter_free(GhosttyFormatter fmt);
GhosttyResult ghostty_formatter_format_alloc(GhosttyFormatter fmt, GhosttyBuffer* buf);
GhosttyResult ghostty_formatter_format_buf(GhosttyFormatter fmt, uint8_t* buf, size_t buf_len, size_t* written);

// Paste safety function
bool ghostty_paste_is_safe(const uint8_t* data, size_t len);

// Build info function
GhosttyResult ghostty_build_info(GhosttyBuildInfoField field, GhosttyString* out);

// Default allocator
GhosttyAllocator* ghostty_allocator_default(void);
"""
