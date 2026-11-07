/* SPDX-License-Identifier: Apache-2.0 */
#ifndef SYNCWATCH_PREROLL_LIMITS_H
#define SYNCWATCH_PREROLL_LIMITS_H

#define SW_CUE_BYTE_LIMIT (4 * 1024 * 1024)
#define SW_CUE_COUNT_LIMIT 64
/* Includes a queue node, AVBuffer and AVBufferRef. The configured parser checks this bound. */
#define SW_CUE_PACKET_OVERHEAD 256

#endif
