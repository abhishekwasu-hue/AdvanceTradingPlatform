# AMW Strategy Lab worker for R. Runs inside the process sandbox (no network, nobody, rlimits, empty env).
# Inputs (written by the host into the private work dir): ctx_data.R (contexts as R literals), strategy.R, mode.txt.
# Output: one line "@@AMW@@{json}" on stdout.
local({
  mode <- readLines("mode.txt", warn = FALSE)[1]
  to_json <- function(x) {
    esc <- function(s) paste0('"', gsub('[\u0001-\u001f]', ' ', gsub('"', '\\\\"', gsub('\\\\', '\\\\\\\\', s))), '"')
    if (is.null(x)) return("null")
    if (is.list(x)) {
      nm <- names(x)
      if (!is.null(nm) && all(nm != "")) {
        return(paste0("{", paste(vapply(seq_along(x), function(i) paste0(esc(nm[i]), ":", to_json(x[[i]])), ""), collapse = ","), "}"))
      }
      return(paste0("[", paste(vapply(x, to_json, ""), collapse = ","), "]"))
    }
    if (length(x) == 0) return("null")
    if (length(x) > 1) return(paste0("[", paste(vapply(as.list(x), to_json, ""), collapse = ","), "]"))
    if (is.na(x)) return("null")
    if (is.logical(x)) return(if (x) "true" else "false")
    if (is.numeric(x)) { if (!is.finite(x)) return("null"); return(format(x, digits = 15, scientific = FALSE)) }
    esc(as.character(x))
  }
  amw <- list(
    isna = function(x) is.null(x) || is.na(x),
    last = function(s, n = 0) if (length(s) > n) s[length(s) - n] else NA_real_,
    prev = function(s, n = 1) if (length(s) > n) s[length(s) - n] else NA_real_,
    highest = function(s, n) { w <- tail(s, n); if (length(w) < n || anyNA(w)) NA_real_ else max(w) },
    lowest = function(s, n) { w <- tail(s, n); if (length(w) < n || anyNA(w)) NA_real_ else min(w) },
    crossover = function(a, b) { v <- c(tail(a, 2), tail(b, 2)); if (length(v) < 4 || anyNA(v)) NA else (v[2] > v[4] && v[1] <= v[3]) },
    crossunder = function(a, b) { v <- c(tail(a, 2), tail(b, 2)); if (length(v) < 4 || anyNA(v)) NA else (v[2] < v[4] && v[1] >= v[3]) }
  )
  env <- new.env(parent = baseenv())
  assign("amw", amw, envir = env)
  for (f in c("sd", "var", "median", "quantile", "cor", "lm", "coef", "residuals", "fitted", "predict", "t.test", "na.omit", "complete.cases")) {
    assign(f, get(f, envir = asNamespace("stats")), envir = env)
  }
  assign("tail", utils::tail, envir = env); assign("head", utils::head, envir = env)
  res <- list(runtime_version = paste("R", getRversion()), check = list(ok = TRUE, errors = list()), results = list())
  ok <- tryCatch({ sys.source("strategy.R", envir = env); TRUE },
                 error = function(e) { res$check <<- list(ok = FALSE, errors = list(conditionMessage(e))); FALSE })
  if (ok && !exists("evaluate", envir = env, inherits = FALSE)) { res$check <- list(ok = FALSE, errors = list("define evaluate <- function(ctx)")); ok <- FALSE }
  if (ok && mode == "run") {
    source("ctx_data.R", local = TRUE)
    for (c in contexts) {
      r <- tryCatch(list(raw = get("evaluate", envir = env)(c), error = NULL, logs = ""),
                    error = function(e) list(raw = NULL, error = substr(conditionMessage(e), 1, 300), logs = ""))
      res$results[[length(res$results) + 1]] <- r
    }
  }
  cat("@@AMW@@", to_json(res), "\n", sep = "")
})
