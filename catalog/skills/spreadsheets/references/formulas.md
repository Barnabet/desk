# The formula engine

`sheet_recalc.py`, `sheet_create.py` and `sheet_edit.py` calculate workbooks with a built-in engine written for
this skill (`scripts/_formula.py`, `_functions.py`, `_functions_more.py`). It reads the formulas from the file,
evaluates them in dependency order and writes the results back as the cached values every reader shows (Excel,
LibreOffice, pandas, calamine, Numbers, Google Sheets import). Its results were cross-checked against LibreOffice on
316 formulas (the selftest battery); where LibreOffice and Excel disagree it follows Excel.

## Semantics

- **Types**: numbers, text, booleans, errors (`#NULL!`, `#DIV/0!`, `#VALUE!`, `#REF!`, `#NAME?`, `#NUM!`, `#N/A`,
  `#SPILL!`, `#CALC!`), blanks. Comparison order is numbers < text < booleans; text compares case-insensitively.
  Numbers keep 15 significant digits for display and equality, like Excel (`=0.1+0.2=0.3` is TRUE).
- **Coercion**: `"10"+5` is 15, `TRUE+TRUE` is 2, `"2024-01-31"+1` is a date. Numeric aggregates (SUM, AVERAGE…)
  skip text and booleans inside ranges but coerce them as direct arguments; the A-variants count them.
- **Dates**: serial numbers in the 1900 system (with Excel's phantom 1900-02-29) or the 1904 system when the
  workbook uses it. TODAY()/NOW() use the clock, or `--now`.
- **Implicit intersection**: a plain formula that gets a range where it expects one value takes the cell in its
  own row or column (legacy Excel behaviour), otherwise `#VALUE!`. Array formulas (`{=…}`, Ctrl+Shift+Enter) and
  dynamic-array formulas evaluate whole arrays.
- **Dynamic arrays**: FILTER, SORT, SORTBY, UNIQUE, SEQUENCE, RANDARRAY, XLOOKUP returning rows, TEXTSPLIT,
  VSTACK/HSTACK, TAKE/DROP, CHOOSECOLS/CHOOSEROWS, TOCOL/TOROW, WRAPROWS/WRAPCOLS, EXPAND, TRANSPOSE, MMULT,
  MUNIT, REGEXEXTRACT spill into neighbouring cells; a blocked spill gives `#SPILL!` with the blocking cells
  reported. `A1#` refers to a spill, `@` forces a single value.
- **New formulas behave like Excel 365**: when `sheet_create`/`sheet_edit` write a formula that only works as an
  array (e.g. `=INDEX(A:A, MATCH(1, (B:B="x")*(C:C>5), 0))`), it is stored as an array formula so every Excel
  version computes the same result.
- **Names and tables**: workbook- and sheet-scoped names (ranges, constants, formulas), LET, structured references
  (`Sales[Revenue]`, `Sales[@Qty]`, `[@Qty]` inside the table, `Sales[#Totals]`, `Sales[[#Headers],[Qty]:[Price]]`,
  a bare `Sales` for the data body), 3-D references (`SUM(Jan:Dec!B2)`), unions and intersections, INDIRECT and
  OFFSET (A1 and R1C1), whole columns and rows.
- **Cycles**: a circular reference is reported with its cells; its cells get 0 like Excel. When the workbook
  enables iterative calculation (`calc` op, `"iterate": true`), cycles iterate up to the workbook's count and
  tolerance instead.
- **Errors** propagate like Excel's. Every error result is listed by cell by `sheet_recalc --check` with a reason
  when the engine knows one (missing name, bad reference, blocked spill…).
- **What-if data tables** (`{=TABLE(…)}`) keep their stored results: they are Excel's own feature.

## File forms

Formulas are stored the way Excel writes them, so files open without `#NAME?` or repair prompts: functions added
after Excel 2007 get `_xlfn.` (and `_xlfn._xlws.` for FILTER and SORT), LET/LAMBDA parameters get `_xlpm.`, `A1#`
becomes `_xlfn.ANCHORARRAY(A1)`, `@x` becomes `_xlfn.SINGLE(x)`, and dynamic-array formulas carry Excel's
metadata part. `sheet_read --formulas` shows them back in the form people type.

## Functions (406, plus LET)

- ABS, ACOS, ACOSH, ACOT, ADDRESS, AGGREGATE, ANCHORARRAY, AND, ARABIC, AREAS, ARRAYTOTEXT, ASIN, ASINH, ATAN, ATAN2, ATANH, AVEDEV, AVERAGE, AVERAGEA, AVERAGEIF, AVERAGEIFS
- BASE, BETA.DIST, BETA.INV, BETADIST, BETAINV, BIN2DEC, BIN2HEX, BIN2OCT, BINOM.DIST, BINOMDIST, BITAND, BITLSHIFT, BITOR, BITRSHIFT, BITXOR
- CEILING, CEILING.MATH, CEILING.PRECISE, CELL, CHAR, CHIDIST, CHIINV, CHISQ.DIST, CHISQ.DIST.RT, CHISQ.INV, CHISQ.INV.RT, CHISQ.TEST, CHITEST, CHOOSE, CHOOSECOLS, CHOOSEROWS, CLEAN, CODE, COLUMN, COLUMNS, COMBIN, COMBINA, CONCAT, CONCATENATE, CONFIDENCE, CONFIDENCE.NORM, CONFIDENCE.T, CORREL, COS, COSH, COT, COUNT, COUNTA, COUNTBLANK, COUNTIF, COUNTIFS, COVAR, COVARIANCE.P, COVARIANCE.S, CSC, CUMIPMT, CUMPRINC
- DATE, DATEDIF, DATEVALUE, DAVERAGE, DAY, DAYS, DAYS360, DB, DCOUNT, DCOUNTA, DDB, DEC2BIN, DEC2HEX, DEC2OCT, DECIMAL, DEGREES, DELTA, DEVSQ, DGET, DMAX, DMIN, DOLLAR, DPRODUCT, DROP, DSTDEV, DSTDEVP, DSUM, DVAR, DVARP
- EDATE, EFFECT, ENCODEURL, EOMONTH, ERF, ERF.PRECISE, ERFC, ERFC.PRECISE, ERROR.TYPE, EVEN, EXACT, EXP, EXPAND, EXPON.DIST, EXPONDIST
- F.DIST, F.DIST.RT, F.TEST, FACT, FACTDOUBLE, FALSE, FDIST, FILTER, FIND, FINDB, FISHER, FISHERINV, FIXED, FLOOR, FLOOR.MATH, FLOOR.PRECISE, FORECAST, FORECAST.LINEAR, FORMULATEXT, FREQUENCY, FTEST, FV
- GAMMA, GAMMA.DIST, GAMMA.INV, GAMMADIST, GAMMAINV, GAMMALN, GAMMALN.PRECISE, GAUSS, GCD, GEOMEAN, GESTEP
- HARMEAN, HEX2BIN, HEX2DEC, HEX2OCT, HLOOKUP, HOUR, HSTACK, HYPERLINK, HYPGEOM.DIST, HYPGEOMDIST
- IF, IFERROR, IFNA, IFS, INDEX, INDIRECT, INT, INTERCEPT, IPMT, IRR, ISBLANK, ISERR, ISERROR, ISEVEN, ISFORMULA, ISLOGICAL, ISNA, ISNONTEXT, ISNUMBER, ISO.CEILING, ISODD, ISOWEEKNUM, ISREF, ISTEXT
- KURT
- LARGE, LCM, LEFT, LEFTB, LEN, LENB, LET, LN, LOG, LOG10, LOGINV, LOGNORM.DIST, LOGNORM.INV, LOGNORMDIST, LOOKUP, LOWER
- MATCH, MAX, MAXA, MAXIFS, MDETERM, MEDIAN, MID, MIDB, MIN, MINA, MINIFS, MINUTE, MINVERSE, MIRR, MMULT, MOD, MODE, MODE.MULT, MODE.SNGL, MONTH, MROUND, MULTINOMIAL, MUNIT
- N, NA, NEGBINOM.DIST, NEGBINOMDIST, NETWORKDAYS, NETWORKDAYS.INTL, NOMINAL, NORM.DIST, NORM.INV, NORM.S.DIST, NORM.S.INV, NORMDIST, NORMINV, NORMSDIST, NORMSINV, NOT, NOW, NPER, NPV, NUMBERVALUE
- OCT2BIN, OCT2DEC, OCT2HEX, ODD, OFFSET, OR
- PDURATION, PEARSON, PERCENTILE, PERCENTILE.EXC, PERCENTILE.INC, PERCENTOF, PERCENTRANK, PERCENTRANK.EXC, PERCENTRANK.INC, PERMUT, PERMUTATIONA, PHI, PI, PMT, POISSON, POISSON.DIST, POWER, PPMT, PROB, PRODUCT, PROPER, PV
- QUARTILE, QUARTILE.EXC, QUARTILE.INC, QUOTIENT
- RADIANS, RAND, RANDARRAY, RANDBETWEEN, RANK, RANK.AVG, RANK.EQ, RATE, REGEXEXTRACT, REGEXREPLACE, REGEXTEST, REPLACE, REPLACEB, REPT, RIGHT, RIGHTB, ROMAN, ROUND, ROUNDDOWN, ROUNDUP, ROW, ROWS, RRI, RSQ
- SEARCH, SEARCHB, SEC, SECOND, SEQUENCE, SERIESSUM, SHEET, SHEETS, SIGN, SIN, SINGLE, SINH, SKEW, SKEW.P, SLN, SLOPE, SMALL, SORT, SORTBY, SQRT, SQRTPI, STANDARDIZE, STDEV, STDEV.P, STDEV.S, STDEVA, STDEVP, STDEVPA, STEYX, SUBSTITUTE, SUBTOTAL, SUM, SUMIF, SUMIFS, SUMPRODUCT, SUMSQ, SUMX2MY2, SUMX2PY2, SUMXMY2, SWITCH, SYD
- T, T.DIST, T.DIST.2T, T.DIST.RT, T.INV, T.INV.2T, T.TEST, TAKE, TAN, TANH, TDIST, TEXT, TEXTAFTER, TEXTBEFORE, TEXTJOIN, TEXTSPLIT, TIME, TIMEVALUE, TINV, TOCOL, TODAY, TOROW, TRANSPOSE, TREND, TRIM, TRIMMEAN, TRUE, TRUNC, TTEST, TYPE
- UNICHAR, UNICODE, UNIQUE, UPPER
- VALUE, VALUETOTEXT, VAR, VAR.P, VAR.S, VARA, VARP, VARPA, VLOOKUP, VSTACK
- WEEKDAY, WEEKNUM, WEIBULL, WEIBULL.DIST, WORKDAY, WORKDAY.INTL, WRAPCOLS, WRAPROWS
- XIRR, XLOOKUP, XMATCH, XNPV, XOR
- YEAR, YEARFRAC
- Z.TEST, ZTEST

Notes on a few:

- Criteria (COUNTIF, SUMIFS, AVERAGEIFS, MAXIFS, MINIFS…) accept `">=5"`, `"<>x"`, wildcards `*`, `?`, `~`,
  dates as text, and `"="` for blanks. The database functions (DSUM, DGET…) read a criteria range: rows are OR'ed,
  cells in a row AND'ed, and plain text matches the start of the value like Excel ("Dav" finds "David"; "=Dav"
  only "Dav"). Computed criteria (a formula under a non-field header) give `#VALUE!`.
- VLOOKUP/HLOOKUP/MATCH/XLOOKUP/XMATCH use hashed indexes for exact matches and binary search for sorted ones, so
  thousands of lookups over thousands of rows stay fast.
- TEXT and cell display share one implementation of Excel's number formats (sections, conditions, colours, dates,
  elapsed times, fractions, scaling, `@`).
- ROUND and friends round half away from zero on the decimal value Excel shows (`ROUND(2.675,2)` is 2.68).
- REGEXTEST/REGEXEXTRACT/REGEXREPLACE use Python's `re`; Excel uses PCRE2. Common syntax (classes, groups,
  quantifiers, anchors, lookarounds, `$1` in replacements) is the same; possessive quantifiers and some Unicode
  properties are not.
- RAND, RANDBETWEEN, RANDARRAY give new numbers on every calculation, like Excel.

## Not evaluated

These keep the result stored in the file and are listed by `sheet_recalc` by name and cell (nothing is guessed):
LAMBDA and its helpers (MAP, REDUCE, SCAN, BYROW, BYCOL, MAKEARRAY, ISOMITTED), GROUPBY, PIVOTBY, TRIMRANGE,
GETPIVOTDATA, the CUBE functions, WEBSERVICE, FILTERXML, STOCKHISTORY, IMAGE, INFO, bond and security functions
(PRICE, YIELD, ACCRINT, DURATION, COUP…), complex numbers (IM…), BESSEL, CONVERT, LINEST, LOGEST, GROWTH,
FORECAST.ETS, and external workbook links (`[Book2.xlsx]Sheet1!A1` keeps the linked value). A cell whose formula
depends on one of them uses its stored value. `--engine libreoffice` recalculates with LibreOffice instead, which
knows many of these (not the Excel 365 LAMBDA family); `--engine auto` does so only when the built-in engine meets
one. With `--check`, LibreOffice recalculates a temporary copy (nothing is written), and `--compare` sets its
results against the stored ones. The report always names the engine that ran; `--now` applies to the built-in
engine only.

## LibreOffice differences (when using `--engine libreoffice`)

LibreOffice counts booleans in ranges as numbers (`SUM` over a TRUE cell adds 1), concatenates TRUE as `1`,
rounds PERCENTRANK instead of truncating, formats DOLLAR negatives with a minus, returns `#VALUE!` from DGET with
several matches (Excel: `#NUM!`), and matches database text criteria exactly. The built-in engine follows Excel in
all of these; say which engine you used when results differ.
